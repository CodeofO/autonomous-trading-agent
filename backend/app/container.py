"""의존성 조립. Settings 하나로 전체 시스템을 만든다."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

from sqlalchemy import delete, func, select

from app.agent.llm import LLMClient
from app.agent.runner import AgentRunner
from app.agent.tools import ToolBox
from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import KST, Clock, RealClock, SimulatedClock
from app.core.config import Settings
from app.core.logging import register_secret
from app.core.market_calendar import MarketCalendar
from app.db.models import EquitySnapshot, Order, StrategyConfig, WatchlistItem
from app.db.session import Database
from app.engine.engine import TradingEngine
from app.engine.events import EventHub
from app.engine.scheduler import Scheduler
from app.engine.settings_store import SettingsStore
from app.market.mock_world import MockDataProvider, MockWorld
from app.market.public_data import PublicDataProvider
from app.market.store import PriceStore
from app.market.toss import TossClient, TossMarketProvider
from app.market.types import MarketDataProvider
from app.research.dart import DartClient
from app.research.naver import NaverNewsClient
from app.research.service import ResearchService
from app.risk.rules import RiskConfig

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    clock: Clock
    cal: MarketCalendar
    store: PriceStore
    broker: PaperBroker
    hub: EventHub
    kv: SettingsStore
    research: ResearchService
    engine: TradingEngine
    runner: AgentRunner
    scheduler: Scheduler
    world: MockWorld | None
    secret_key: bytes
    toss: TossMarketProvider | None = None

    @property
    def data_source(self) -> str:
        return self.settings.resolved_data_source

    @property
    def clock_mode(self) -> str:
        return self.settings.resolved_clock_mode


def _sim_start(settings: Settings, cal: MarketCalendar, kv: SettingsStore) -> datetime:
    saved = kv.get("sim_clock").get("now")
    if saved:
        return datetime.fromisoformat(saved)
    if settings.sim_start:
        return datetime.fromisoformat(settings.sim_start).replace(tzinfo=KST)
    now = datetime.now(KST)
    d = now.date() if cal.is_trading_day(now.date()) else cal.next_trading_day(now.date())
    return datetime.combine(d, time(8, 0), KST)


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


def _reset_on_source_change(
    db: Database, kv: SettingsStore, broker: PaperBroker, hub: EventHub, source: str
) -> None:
    """데이터 소스가 바뀌면(예: 가상 시장 → 토스 실시세) 모의계좌를 새로 시작한다(D-25).

    가상 종목 포지션을 실제 시세로 평가할 수 없으므로 계좌·미체결·자산곡선·가상 관심종목을 정리한다.
    주문·체결·결정 기록과 감사 로그는 그대로 남긴다.
    """
    prev = kv.get("account_source").get("source")
    if prev == source:
        return
    if prev is not None:
        broker.reset()
        with db.session() as s:
            for o in s.scalars(
                select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
            ):
                o.status, o.reject_reason = "CANCELED", "data-source-changed"
            s.execute(delete(EquitySnapshot))
            s.execute(delete(WatchlistItem).where(WatchlistItem.symbol.like("MOCK%")))
        kv.set("sod", {})
        hub.audit("ACCOUNT_RESET", f"데이터 소스 변경 {prev} → {source}: 모의계좌 초기화", None)
        hub.chat(
            "system",
            "text",
            f"🔄 데이터 소스가 {prev.upper()} → {source.upper()} 로 바뀌어 모의계좌를 새로 시작했어요 "
            f"(초기 자금 {broker.state['initial_cash']:,.0f}원).",
        )
    kv.set("account_source", {"source": source})


def build_container(settings: Settings, *, create_schema: bool = False) -> Container:
    db = Database(settings.database_url)
    if create_schema:
        db.create_all()
    kv = SettingsStore(db)
    cal = MarketCalendar()
    source = settings.resolved_data_source
    clock_mode = settings.resolved_clock_mode
    clock: Clock = SimulatedClock(_sim_start(settings, cal, kv)) if clock_mode == "simulated" else RealClock()

    world: MockWorld | None = None
    toss: TossMarketProvider | None = None
    provider: MarketDataProvider
    if source == "mock":
        world = MockWorld(settings.mock_seed, cal)
        provider = MockDataProvider(world, clock)
    elif source == "toss":
        assert settings.toss_client_id and settings.toss_client_secret
        toss = TossMarketProvider(
            TossClient(
                settings.toss_client_id.get_secret_value(), settings.toss_client_secret.get_secret_value()
            ),
            clock,
        )
        provider = toss
    else:
        assert settings.data_go_kr_service_key is not None
        provider = PublicDataProvider(settings.data_go_kr_service_key.get_secret_value())
    store = PriceStore(db, provider)

    dart = (
        DartClient(settings.dart_api_key.get_secret_value(), db)
        if settings.has_dart and settings.dart_api_key and source != "mock"
        else None
    )
    news = None
    if settings.has_naver and source != "mock":
        assert settings.naver_client_id and settings.naver_client_secret
        news = NaverNewsClient(
            settings.naver_client_id.get_secret_value(), settings.naver_client_secret.get_secret_value()
        )
    research = ResearchService(clock, dart=dart, news=news, world=world)

    broker = PaperBroker(
        db,
        store,
        cal,
        clock,
        initial_cash=settings.paper_initial_cash,
        trading_mode=settings.trading_mode,
        costs=PaperCosts(
            settings.paper_commission_rate,
            settings.paper_sell_tax_rate,
            settings.paper_slippage_bps,
            settings.paper_max_volume_participation,
        ),
        quotes=toss,  # 토스면 실시간 체결
    )
    hub = EventHub(db, clock)
    seed_defaults(db, kv)
    _reset_on_source_change(db, kv, broker, hub, source)
    engine = TradingEngine(db, clock, cal, store, broker, hub, kv, toss=toss)

    llm = None
    if settings.has_llm:
        assert settings.llm_api_key is not None
        llm = LLMClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_api_key.get_secret_value(),
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_sec,
        )
    toolbox = ToolBox(
        engine.portfolio,
        store,
        research,
        clock,
        screener=toss.screen if toss else None,
        quote=broker.get_quote if toss else None,
    )
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
    )
    engine.runner = runner
    scheduler = Scheduler(engine, clock_mode, settings.sim_speed)
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
        world,
        _secret_key(settings),
        toss,
    )
