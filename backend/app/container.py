"""의존성 조립. Settings 하나로 전체 시스템을 만든다.

시세는 토스증권 Open API, 시계는 실제 한국 시각.
주문은 TRADING_MODE 에 따라 자체 모의계좌(PaperBroker, 기본) 또는 토스증권 실계좌(TossLiveBroker, D-34).
테스트는 `toss_client`·`trading_client`·`clock` 을 주입해 네트워크 없이 조립한다.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, or_, select

from app.agent.profiles import active_profile, build_llm
from app.agent.runner import AgentRunner
from app.agent.tools import ToolBox
from app.brokers.base import TERMINAL
from app.brokers.paper import PaperBroker, PaperCosts
from app.brokers.toss_live import TossLiveBroker, TossTradingClient
from app.core.clock import KST, Clock, RealClock
from app.core.config import Settings
from app.core.logging import register_secret
from app.core.market_calendar import MarketCalendar
from app.db.models import (
    AgentDecision,
    AgentRun,
    AuditEvent,
    ChatMessage,
    DailyPrice,
    DailyReport,
    EquitySnapshot,
    Fill,
    Order,
    StrategyConfig,
    WatchlistItem,
)
from app.db.session import Database
from app.engine.engine import TradingEngine
from app.engine.events import SYSTEM, EventHub
from app.engine.push import PushNotifier
from app.engine.scheduler import Scheduler
from app.engine.settings_store import SettingsStore
from app.engine.trades import trade_ledger
from app.market.indicators import MarketIndicators
from app.market.store import PriceStore
from app.market.toss import TossClient, TossMarketProvider
from app.research.dart import DartClient
from app.research.naver import NaverNewsClient
from app.research.service import ResearchService
from app.risk.rules import RiskConfig

log = logging.getLogger(__name__)

SOURCE = "toss"  # 토스 시세 + 모의계좌
LIVE_SOURCE = "toss-live"  # 토스 시세 + 토스 실계좌
_MODE_KO = {SOURCE: "모의투자", LIVE_SOURCE: "실거래"}


@dataclass
class Container:
    settings: Settings
    db: Database
    clock: Clock
    cal: MarketCalendar
    store: PriceStore
    broker: PaperBroker | TossLiveBroker
    hub: EventHub
    kv: SettingsStore
    research: ResearchService
    engine: TradingEngine
    runner: AgentRunner
    scheduler: Scheduler
    toss: TossMarketProvider
    secret_key: bytes
    push: PushNotifier
    indicators: MarketIndicators
    trading: TossTradingClient | None = None  # 실거래 모드의 토스 주문 클라이언트


def _secret_key(settings: Settings) -> bytes:
    if settings.app_secret_key and settings.app_secret_key.get_secret_value():
        return settings.app_secret_key.get_secret_value().encode()
    path = Path(settings.data_dir) / ".secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    key = path.read_text().strip()
    register_secret(key)
    return key.encode()


def seed_defaults(db: Database, kv: SettingsStore) -> None:
    if not kv.get("risk"):
        kv.set("risk", RiskConfig().model_dump())
    if not kv.get("engine"):
        kv.set("engine", {"status": "RUNNING"})
    with db.session() as s:
        if (s.scalar(select(func.count()).select_from(StrategyConfig)) or 0) == 0:
            s.add_all(
                [
                    StrategyConfig(
                        name="이동평균 크로스", type="ma_cross", enabled=True, params={"short": 5, "long": 20}
                    ),
                    StrategyConfig(
                        name="RSI 역추세",
                        type="rsi",
                        enabled=True,
                        params={"period": 14, "lower": 30, "upper": 70},
                    ),
                ]
            )


def purge_legacy(db: Database, since: datetime | None) -> datetime:
    """가상 시장 시절 기록을 전부 지운다(사용자 요청, D-28).

    대상: 세션·결정·주문·체결·감사 로그·채팅·자산곡선·리포트·MOCK 시세·MOCK 관심종목.
    since 가 없으면 토스 전환 기록(ACCOUNT_RESET) 시각을 기준으로 삼는다. 기준 시각을 돌려준다.
    """
    with db.session() as s:
        if since is None:
            since = s.scalar(
                select(func.max(AuditEvent.ts)).where(AuditEvent.type == "ACCOUNT_RESET")
            ) or datetime.now(UTC)
        # 가속 시뮬레이션 시절 기록은 미래 날짜로 찍혀 있을 수 있다 → 실제 현재 이후도 목업으로 본다
        future = datetime.now(UTC) + timedelta(hours=1)

        def stale(col: Any) -> Any:
            return or_(col < since, col > future)

        mock_runs = select(AgentDecision.run_id).where(AgentDecision.symbol.like("MOCK%"))
        old_run_ids = list(
            s.scalars(
                select(AgentRun.id).where(
                    or_(
                        stale(AgentRun.started_at),
                        AgentRun.id.in_(mock_runs),
                        # 결정·채팅이 하나도 남지 않은 빈 세션(이전 정리에서 내용만 지워진 경우)
                        and_(
                            AgentRun.id.not_in(select(AgentDecision.run_id)),
                            AgentRun.id.not_in(
                                select(ChatMessage.run_id).where(ChatMessage.run_id.is_not(None))
                            ),
                        ),
                    )
                )
            )
        )
        old_orders = select(Order.id).where(
            or_(stale(Order.created_at), Order.symbol.like("MOCK%"), Order.run_id.in_(old_run_ids))
        )
        # 외래키 순서: 결정(→주문·세션) → 체결(→주문) → 주문(→세션) → 세션
        s.execute(
            delete(AgentDecision).where(
                or_(AgentDecision.run_id.in_(old_run_ids), AgentDecision.order_id.in_(old_orders))
            )
        )
        s.execute(delete(Fill).where(Fill.order_id.in_(old_orders)))
        s.execute(delete(Order).where(Order.id.in_(old_orders)))
        s.execute(delete(AgentRun).where(AgentRun.id.in_(old_run_ids)))
        s.execute(delete(AuditEvent).where(stale(AuditEvent.ts), AuditEvent.type != "ACCOUNT_RESET"))
        s.execute(delete(ChatMessage).where(stale(ChatMessage.ts)))
        s.execute(delete(EquitySnapshot).where(stale(EquitySnapshot.ts)))
        s.execute(delete(DailyReport).where(DailyReport.created_at < since))
        s.execute(delete(DailyPrice).where(DailyPrice.symbol.like("MOCK%")))
        s.execute(delete(WatchlistItem).where(WatchlistItem.symbol.like("MOCK%")))
    return since


def _reset_on_source_change(
    db: Database, kv: SettingsStore, broker: PaperBroker | TossLiveBroker, hub: EventHub, source: str
) -> None:
    """데이터 소스·거래 모드가 바뀌면 계좌 기준 시각(since)을 새로 잡는다.

    - 가상 시장 → 토스: 모의계좌를 새로 시작하고 이전 기록을 지운다(D-25, D-28).
    - 모의투자 ↔ 실거래: 기록은 남기고 기준 시각만 바꾼다. 에이전트 기억·매매 내역·자산 곡선은 since 이후만 본다.
      남아 있던 미체결 주문 기록은 '모드 전환'으로 정리한다(D-34).
    """
    state = kv.get("account_source")
    prev = state.get("source")
    if prev == source:
        if state.get("purged") != 4:  # 이전 버전에서 이미 전환된 DB → 남아 있는 목업 기록 정리 (정리 규칙 v4)
            try:
                since = purge_legacy(db, None)
            except Exception:  # 정리 실패가 기동을 막지 않게 (다음 기동 때 다시 시도)
                log.exception("목업 기록 정리 실패")
                return
            kv.set("account_source", {"source": source, "since": since.isoformat(), "purged": 4})
        return
    since = hub.clock.now()
    if prev in _MODE_KO:
        n = _close_open_orders(db, broker)
        kv.set("sod", {})
        msg = f"거래 모드 전환 {_MODE_KO[prev]} → {_MODE_KO[source]}"
        hub.audit("ACCOUNT_SWITCH", msg, None, closed_orders=n)
        extra = (
            " 토스증권에 남아 있을 수 있는 실제 미체결 주문은 토스 앱에서 확인해 주세요."
            if prev == LIVE_SOURCE
            else ""
        )
        hub.chat(
            SYSTEM,
            "text",
            f"🔀 {msg}. 이전 모드의 미체결 주문 기록 {n}건을 정리했고, 이제부터 새 계좌 기준으로 판단해요.{extra}",
        )
    elif prev is not None:
        if isinstance(broker, PaperBroker):
            broker.reset()
        purge_legacy(db, since)
        kv.set("sod", {})
        kv.set("sim_clock", {})
        hub.audit("ACCOUNT_RESET", f"데이터 소스 변경 {prev} → {source}: 계좌 기록 초기화", None)
        hub.chat(SYSTEM, "text", f"🔄 토스증권 실제 시세로 전환했어요. ({_MODE_KO[source]})")
    kv.set("account_source", {"source": source, "since": since.isoformat(), "purged": 4})


def _close_open_orders(db: Database, broker: PaperBroker | TossLiveBroker) -> int:
    """모드 전환 시 이전 모드의 미체결 주문 기록을 취소 상태로 정리한다 (실제 토스 주문은 건드리지 않음)."""
    with db.session() as s:
        rows = list(
            s.scalars(select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"])))
        )
        for o in rows:
            o.status, o.reject_reason = "CANCELED", "account-switch"
    if isinstance(broker, PaperBroker):  # 모의계좌 쪽 미체결도 정리 (다음 체결 처리에서 되살아나지 않게)
        for po in broker.state["orders"].values():
            if po["status"] not in TERMINAL:
                po["status"], po["reject_reason"], po["reserved"] = "CANCELED", "account-switch", 0.0
        broker._save()
    return len(rows)


def announce_boot(c: Container, text: str) -> bool:
    """트레이딩 룸 대화는 하루 단위로 이어진다: 기동 안내는 하루 한 번만 채팅에, 그 뒤 재시작은 감사 로그에만."""
    today = c.clock.now().astimezone(KST).date().isoformat()
    if c.kv.get("boot_notice").get("day") == today:
        c.hub.audit("SYSTEM", "재시작 — " + text.removeprefix("🟢 "), None)
        return False
    c.kv.set("boot_notice", {"day": today})
    c.hub.chat(SYSTEM, "text", text)
    return True


def build_container(
    settings: Settings,
    *,
    create_schema: bool = False,
    toss_client: Any = None,
    trading_client: Any = None,
    clock: Clock | None = None,
) -> Container:
    """`toss_client`(테스트용 가짜)를 주지 않으면 .env 의 토스 키가 필요하다.

    TRADING_MODE=live 이면 토스 실계좌 브로커를 쓴다. `trading_client` 는 테스트용 가짜 주문 클라이언트.
    """
    if toss_client is None:
        settings.require_toss()
        assert settings.toss_client_id and settings.toss_client_secret
        toss_client = TossClient(
            settings.toss_client_id.get_secret_value(), settings.toss_client_secret.get_secret_value()
        )
    db = Database(settings.database_url)
    if create_schema:
        db.create_all()
    kv = SettingsStore(db)
    cal = MarketCalendar()
    clock = clock or RealClock()
    toss = TossMarketProvider(toss_client, clock)
    store = PriceStore(db, toss)

    dart = (
        DartClient(settings.dart_api_key.get_secret_value(), db)
        if settings.has_dart and settings.dart_api_key
        else None
    )
    news = None
    if settings.has_naver:
        assert settings.naver_client_id and settings.naver_client_secret
        news = NaverNewsClient(
            settings.naver_client_id.get_secret_value(), settings.naver_client_secret.get_secret_value()
        )
    research = ResearchService(clock, dart=dart, news=news)

    costs = PaperCosts(
        settings.paper_commission_rate,
        settings.paper_sell_tax_rate,
        settings.paper_slippage_bps,
        settings.paper_max_volume_participation,
    )

    def trades(limit: int = 20) -> dict[str, Any]:
        since = kv.get("account_source").get("since")
        return trade_ledger(db, store.name_of, datetime.fromisoformat(since) if since else None, limit)

    broker: PaperBroker | TossLiveBroker
    trading: TossTradingClient | None = None
    if settings.is_live:
        # 실거래: 사용자가 .env 에서 TRADING_MODE=live + 확인 문구를 넣었을 때만 (Settings 가 검증)
        trading = trading_client or TossTradingClient(toss_client)
        broker = TossLiveBroker(
            trading,
            toss,
            store,
            clock,
            kv,
            trading_mode=settings.trading_mode,
            daily_buy_limit=settings.live_daily_buy_limit,
            account_seq=settings.toss_account_seq,
            costs=costs,
            ledger=lambda: trades(1),
        )
    else:
        broker = PaperBroker(
            db,
            store,
            cal,
            clock,
            initial_cash=settings.paper_initial_cash,
            trading_mode=settings.trading_mode,
            costs=costs,
            quotes=toss,  # 토스 실시간 현재가로 체결
        )
    hub = EventHub(db, clock)
    push = PushNotifier(
        kv,
        settings.vapid_private_key.get_secret_value() if settings.vapid_private_key else None,
        settings.vapid_subject,
        names=store.name_of,
        now=clock.now,
        live=settings.is_live,
    )
    hub.listeners.append(push.on_audit)
    seed_defaults(db, kv)
    _reset_on_source_change(db, kv, broker, hub, LIVE_SOURCE if settings.is_live else SOURCE)
    engine = TradingEngine(db, clock, cal, store, broker, hub, kv, toss=toss)

    llm = build_llm(
        settings, active_profile(kv, settings)
    )  # 무료(LLM_TEST_*)/유료(LLM_*) 중 화면에서 고른 것
    toolbox = ToolBox(engine.portfolio, store, research, clock, screener=toss.screen, quote=broker.get_quote)
    toolbox.toss = toss
    toolbox.trades = trades
    runner = AgentRunner(
        db,
        clock,
        hub,
        toolbox,
        engine.orders,
        kv,
        llm,
        tool_mode=settings.llm_tool_mode,
        max_steps=settings.llm_max_steps,
        strategies=engine.enabled_strategies,
        names=store.name_of,
        live=settings.is_live,
    )
    engine.runner = runner
    scheduler = Scheduler(engine, settings.agent_interval_minutes)
    return Container(
        settings,
        db,
        clock,
        cal,
        store,
        broker,
        hub,
        kv,
        research,
        engine,
        runner,
        scheduler,
        toss,
        _secret_key(settings),
        push,
        MarketIndicators(toss.client, clock),
        trading,
    )
