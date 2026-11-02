"""FastAPI 진입점. 실행: `uvicorn app.main:create_app --factory`"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.api import routes, ws
from app.container import announce_boot, build_container
from app.core.config import BACKEND_DIR, Settings, get_settings
from app.core.logging import setup_logging

log = logging.getLogger(__name__)


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


def create_app(settings: Settings | None = None, **container_overrides: Any) -> FastAPI:
    """`container_overrides`(toss_client, clock)는 테스트 전용."""
    settings = settings or get_settings()
    setup_logging(settings.log_level, settings.secret_values())
    in_memory = settings.database_url in ("sqlite://", "sqlite:///:memory:")
    if not in_memory:
        _migrate(settings.database_url)
    container = build_container(settings, create_schema=in_memory, **container_overrides)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container
        log.info(
            "기동: TRADING_MODE=%s 시세=토스증권(조회 전용) LLM=%s 장중판단=%s분",
            settings.trading_mode,
            settings.llm_model if settings.has_llm else "없음(규칙 기반)",
            settings.agent_interval_minutes,
        )
        resume: asyncio.Task[None] | None = None
        await c.engine.refresh_calendar()
        try:
            await c.engine.sync_data()
        except Exception:
            log.exception("초기 시세 동기화 실패")
        if settings.enable_scheduler:
            c.scheduler.start()
            boot = (
                f"🟢 시스템 기동 — 시세: 토스증권 실시간, 장중 판단 {settings.agent_interval_minutes}분 간격, "
                f"LLM: {settings.llm_model if settings.has_llm else '미설정(규칙 기반)'}"
            )
            announce_boot(c, boot)
            resume = asyncio.create_task(c.runner.resume_pending())  # 재시작 전에 못 한 답장
        yield
        if resume is not None and not resume.done():
            resume.cancel()
        c.scheduler.shutdown()
        await c.toss.client.aclose()

    app = FastAPI(
        title="AI Paper Fund Manager",
        version="0.2.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = container
    app.include_router(routes.router)
    app.include_router(ws.router)
    return app
