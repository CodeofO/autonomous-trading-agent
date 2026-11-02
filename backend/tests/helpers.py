"""테스트 공용 빌더."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import KST, SimulatedClock
from app.core.market_calendar import MarketCalendar
from app.db.session import Database
from app.market.store import PriceStore
from app.market.types import Bar


class StaticProvider:
    """메모리 일봉 공급자 (테스트 전용)."""

    name = "static"

    def __init__(self, bars: list[Bar] | None = None) -> None:
        self.bars = list(bars or [])

    async def fetch_day(self, day: date) -> list[Bar]:
        return [b for b in self.bars if b.day == day]

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return sorted(
            (b for b in self.bars if b.symbol == symbol and start <= b.day <= end), key=lambda b: b.day
        )


def bar(
    symbol: str, day: date, o: float, h: float, low: float, c: float, v: float = 1_000_000, name: str = ""
) -> Bar:
    return Bar(
        symbol=symbol,
        day=day,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        value=v * c,
        market_cap=c * 1e7,
        listed_shares=1e7,
        name=name or symbol,
        market="KOSPI",
    )


@dataclass
class Env:
    db: Database
    clock: SimulatedClock
    cal: MarketCalendar
    store: PriceStore
    provider: StaticProvider
    broker: PaperBroker


def make_env(
    start: datetime | None = None,
    cash: float = 10_000_000,
    costs: PaperCosts | None = None,
    bars: list[Bar] | None = None,
) -> Env:
    db = Database("sqlite://")
    db.create_all()
    clock = SimulatedClock(start or datetime(2026, 11, 2, 8, 40, tzinfo=KST))
    cal = MarketCalendar()
    provider = StaticProvider(bars)
    store = PriceStore(db, provider)
    store.upsert(provider.bars)
    broker = PaperBroker(db, store, cal, clock, initial_cash=cash, costs=costs or PaperCosts())
    return Env(db, clock, cal, store, provider, broker)


def make_container(
    tmp_path: Any,
    start: datetime | None = None,
    *,
    create_schema: bool = True,
    database_url: str = "sqlite://",
    **settings: Any,
) -> Any:
    """가짜 토스 클라이언트 + 고정 시계로 전체 시스템 조립 (네트워크 없음)."""
    from app.container import build_container
    from app.core.config import Settings
    from tests.fake_toss import FakeTossClient

    clock = SimulatedClock(start or datetime(2026, 11, 2, 10, 0, tzinfo=KST))
    s = Settings(database_url=database_url, data_dir=tmp_path, enable_scheduler=False, **settings)
    c = build_container(s, create_schema=create_schema, toss_client=FakeTossClient(clock), clock=clock)
    return c
