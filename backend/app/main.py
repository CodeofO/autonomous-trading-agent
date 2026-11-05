"""FastAPI 진입점. 실행: `uvicorn app.main:create_app --factory`"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.api import llm_extra, routes, ws
from app.container import Container, announce_boot, build_container
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging
from app.db.migrate import migrate
from app.engine.events import SYSTEM

log = logging.getLogger(__name__)


_migrate = migrate  # 예전 이름 (테스트·스크립트 호환)


GZIP_MIN_BYTES = 1024  # 이보다 작은 응답은 압축하지 않는다 (B4). WebSocket(/ws)은 압축 대상이 아니다
GZIP_LEVEL = 4  # 6 대비 압축 시간 절반·크기 +4~7% (압축은 이벤트 루프에서 돈다)


def _in_memory(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:")


def _build_containers(settings: Settings, overrides: dict[str, Any]) -> dict[str, Container]:
    """국장 스택(+ 켜져 있으면 미장 스택). 파일 DB 면 먼저 Alembic 마이그레이션."""
    in_memory = _in_memory(settings.database_url)
    if not in_memory:
        _migrate(settings.database_url)
    container = build_container(settings, create_schema=in_memory, **overrides)
    containers: dict[str, Container] = {"KR": container}
    if settings.us_market_enabled:
        # 미장 스택 (D-48): 별도 DB·계좌·엔진·에이전트·스케줄러, 토스 클라이언트는 국장과 같은 인스턴스.
        # 거래 모드는 US_TRADING_MODE (D-55). trading_client 는 테스트용 가짜 주문 클라이언트
        us_url = settings.us_db_url()
        if not _in_memory(us_url):
            _migrate(us_url)
        containers["US"] = build_container(
            settings,
            create_schema=_in_memory(us_url),
            market="US",
            shared=container,
            trading_client=overrides.get("trading_client"),
            clock=overrides.get("clock"),
        )
    return containers


def _lifespan(settings: Settings, containers: dict[str, Container]) -> Any:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = containers["KR"]
        resume = await _start_kr(c, settings)
        us_resume: asyncio.Task[None] | None = None
        if (u := containers.get("US")) is not None:
            us_resume = await _start_us(u)
        yield
        for t in (resume, us_resume):
            if t is not None and not t.done():
                t.cancel()
        if u is not None:
            u.scheduler.shutdown()
        c.scheduler.shutdown()
        # 주문 클라이언트는 국장·미장이 같이 쓸 수 있다 — 한 번씩만 닫는다
        traders = {id(x.trading): x.trading for x in containers.values() if x.trading is not None}
        for trader in traders.values():
            await trader.aclose()
        await c.toss.client.aclose()

    return lifespan


def create_app(settings: Settings | None = None, **container_overrides: Any) -> FastAPI:
    """`container_overrides`(toss_client, clock)는 테스트 전용."""
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    containers = _build_containers(settings, container_overrides)
    app = FastAPI(
        title="AI Fund Manager",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan(settings, containers),
    )
    # 큰 목록 응답(채팅·감사 로그·결정·리포트)은 gzip 으로 4~8배 줄어든다. 브라우저 fetch 는 알아서 푼다.
    # Starlette GZip 은 http 요청에만 동작한다(WebSocket 은 그대로, text/event-stream 은 제외).
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_BYTES, compresslevel=GZIP_LEVEL)
    app.state.container = containers["KR"]  # 국장 (기존 코드 호환)
    app.state.containers = containers
    app.include_router(routes.router)
    if "US" in containers:  # 미장 스택: 같은 API 를 /api/us 아래에 (전역 API 는 /api 에만, D-48)
        app.include_router(routes.us_router())
    app.include_router(llm_extra.router)
    app.include_router(ws.router)
    return app


async def _start_kr(c: Container, settings: Settings) -> asyncio.Task[None] | None:
    """국장 스택 기동: 캘린더·시세 동기화 → 데이터 정리 → (실거래 계좌 확인) → 스케줄러 → 기동 안내."""
    # 실제로 쓰는 모델(무료/유료 선택·유료 모델 선택 반영). .env 의 LLM_MODEL 이 아니다
    active_llm = c.runner.llm.model if c.runner.llm else "없음(규칙 기반)"
    log.info(
        "기동: TRADING_MODE=%s US_TRADING_MODE=%s 시세=토스증권(조회 전용) LLM=%s %s",
        settings.trading_mode,
        settings.us_trading_mode if settings.us_market_enabled else "off",
        active_llm,
        c.scheduler.describe(),
    )
    c.push.bind_loop(asyncio.get_running_loop())
    await c.engine.refresh_calendar()
    try:
        await c.engine.sync_data()
    except Exception:
        log.exception("초기 시세 동기화 실패")
    await c.scheduler.maintenance()  # 하루 한 번 정리를 기동 때도 한 번 (실패해도 기동은 계속)
    interrupted = c.runner.mark_interrupted()  # 재시작으로 끊긴 세션 정리
    _start_lab(c)
    if settings.is_live:
        await _check_live_account(c)
    if not settings.enable_scheduler:
        return None
    c.scheduler.start()
    c.scheduler.recover(interrupted)  # 배포·재시작이 판단 시각과 겹쳐 끊긴 판단은 한 번 다시
    account = "🔴 토스증권 실계좌(실거래)" if settings.is_live else "모의투자 계좌"
    boot = (
        f"🟢 시스템 기동 — 주문: {account}, 시세: 토스증권 실시간, "
        f"{c.scheduler.describe()}, "
        f"LLM: {active_llm}"
    )
    announce_boot(c, boot)
    return asyncio.create_task(c.runner.resume_pending())  # 재시작 전에 못 한 답장


async def _start_us(u: Container) -> asyncio.Task[None] | None:
    """미장 스택 기동: 캘린더·시세 동기화 → 스케줄러 → 기동 안내. 실패해도 국장 기동은 막지 않는다."""
    s = u.settings
    u.push.bind_loop(asyncio.get_running_loop())
    try:
        await u.engine.refresh_calendar()
        await u.engine.sync_data()
    except Exception:
        log.exception("미장 초기 시세 동기화 실패")
    await u.scheduler.maintenance()
    interrupted = u.runner.mark_interrupted()
    _start_lab(u)
    if s.us_is_live:
        await _check_live_account(u)
    if not s.enable_scheduler:
        return None
    u.scheduler.start()
    u.scheduler.recover(interrupted)
    model = u.runner.llm.model if u.runner.llm else "미설정(규칙 기반)"
    nxt = u.scheduler.next_runs(1)
    when = f", 다음 일정 {nxt[0]['at'][5:16].replace('T', ' ')} KST" if nxt else ""
    account = "🔴 토스증권 실계좌(미국 주식 실거래)" if s.us_is_live else "미국 주식 모의투자 계좌(달러)"
    announce_boot(
        u,
        f"🟢 미장 시스템 기동 — 주문: {account}, 시세: 토스증권 실시간, "
        f"{u.scheduler.describe()}, LLM: {model}{when}",
    )
    return asyncio.create_task(u.runner.resume_pending())


def _start_lab(c: Container) -> None:
    """전략 실험실 (D-62): 켜 둔 시나리오 스택을 미리 만든다 (기록 DB 마이그레이션 포함). 실패해도 기동은 계속."""
    if c.lab is None:
        return
    try:
        c.lab.ensure()
        for st in c.lab.stacks.values():  # 시나리오도 끊긴 세션은 정리만 (다음 차례에 다시 돈다)
            st.runner.mark_interrupted()
        if c.lab.stacks:
            log.info("전략 시나리오(%s): %s", c.code, ", ".join(c.lab.stacks))
    except Exception:
        log.exception("전략 시나리오 준비 실패")


async def _check_live_account(c: Any) -> None:
    """실거래: 주문 계좌를 확인한다. 확인이 안 되면 엔진을 일시정지하고 이유를 알린다(주문 실패 반복 방지)."""
    try:
        await c.broker.ensure_account()
        c.hub.audit("LIVE", "실거래 모드 — 토스증권 계좌 확인 완료", None)  # 시장마다 자기 감사 로그에
    except Exception as e:
        log.warning("실거래 계좌 확인 실패: %s", e)
        c.engine.pause()
        c.hub.chat(
            SYSTEM,
            "text",
            f"⚠️ 실거래 계좌를 확인하지 못해 자동매매를 일시정지했어요: {str(e)[:200]} "
            "(.env 의 TOSS_ACCOUNT_SEQ·허용 IP 를 확인한 뒤 재시작하거나 '재개'를 눌러 주세요)",
        )
