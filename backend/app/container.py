"""의존성 조립. Settings 하나로 전체 시스템을 만든다.

시세는 토스증권 Open API(조회 전용), 시계는 실제 한국 시각, 주문은 자체 모의계좌(PaperBroker).
테스트는 `toss_client`·`clock` 을 주입해 네트워크 없이 조립한다.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.agent.llm import LLMClient
from app.agent.runner import AgentRunner
from app.agent.tools import ToolBox
from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import Clock, RealClock
from app.core.config import Settings
from app.core.logging import register_secret
from app.core.market_calendar import MarketCalendar
from app.db.models import (
    ChatMessage,
    DailyPrice,
    DailyReport,
    EquitySnapshot,
    Order,
    StrategyConfig,
    WatchlistItem,
)
from app.db.session import Database
from app.engine.engine import TradingEngine
from app.engine.events import SYSTEM, EventHub
from app.engine.scheduler import Scheduler
from app.engine.settings_store import SettingsStore
from app.market.store import PriceStore
from app.market.toss import TossClient, TossMarketProvider
from app.research.dart import DartClient
from app.research.naver import NaverNewsClient
from app.research.service import ResearchService
from app.risk.rules import RiskConfig

log = logging.getLogger(__name__)

SOURCE = "toss"


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
    toss: TossMarketProvider
    secret_key: bytes


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


def _reset_on_source_change(db: Database, kv: SettingsStore, broker: PaperBroker, hub: EventHub) -> None:
    """이전 데이터 소스(가상 시장 등)에서 토스 실데이터로 처음 넘어오면 모의계좌와 목업 흔적을 정리한다(D-25, D-28).

    정리: 현금·포지션 초기화, 미체결 취소, 자산곡선·일일 리포트·가상 종목 시세·가상 관심종목·트레이딩 룸 채팅.
    보존: 주문·체결·에이전트 결정·감사 로그(기록용).
    """
    prev = kv.get("account_source").get("source")
    if prev == SOURCE:
        return
    if prev is not None:
        broker.reset()
        with db.session() as s:
            for o in s.scalars(
                select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
            ):
                o.status, o.reject_reason = "CANCELED", "data-source-changed"
            s.execute(delete(EquitySnapshot))
            s.execute(delete(DailyReport))
            s.execute(delete(ChatMessage))
            s.execute(delete(DailyPrice).where(DailyPrice.symbol.like("MOCK%")))
            s.execute(delete(WatchlistItem).where(WatchlistItem.symbol.like("MOCK%")))
        kv.set("sod", {})
        kv.set("sim_clock", {})
        hub.audit("ACCOUNT_RESET", f"데이터 소스 변경 {prev} → {SOURCE}: 모의계좌 초기화", None)
        hub.chat(
            SYSTEM,
            "text",
            f"🔄 토스증권 실제 시세로 전환했어요. 모의계좌를 새로 시작합니다 (초기 자금 {broker.state['initial_cash']:,.0f}원).",
        )
    kv.set("account_source", {"source": SOURCE})


def build_container(
    settings: Settings,
    *,
    create_schema: bool = False,
    toss_client: Any = None,
    clock: Clock | None = None,
) -> Container:
    """`toss_client`(테스트용 가짜)를 주지 않으면 .env 의 토스 키가 필요하다."""
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
        quotes=toss,  # 토스 실시간 현재가로 체결
    )
    hub = EventHub(db, clock)
    seed_defaults(db, kv)
    _reset_on_source_change(db, kv, broker, hub)
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
    toolbox = ToolBox(engine.portfolio, store, research, clock, screener=toss.screen, quote=broker.get_quote)
    toolbox.toss = toss
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
    )
