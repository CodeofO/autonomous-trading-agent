"""일봉 저장소. 공급자에서 받은 일봉을 DB 에 캐시하고 조회한다."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models import DailyPrice
from app.db.session import Database
from app.market.types import Bar, MarketDataProvider

log = logging.getLogger(__name__)


def _to_bar(r: DailyPrice) -> Bar:
    return Bar(
        symbol=r.symbol,
        day=r.day,
        open=r.open,
        high=r.high,
        low=r.low,
        close=r.close,
        volume=r.volume,
        value=r.value,
        market_cap=r.market_cap,
        listed_shares=r.listed_shares,
        name=r.name,
        market=r.market,
    )


class PriceStore:
    def __init__(self, db: Database, provider: MarketDataProvider) -> None:
        self.db = db
        self.provider = provider

    # ------------------------------------------------------------- 쓰기
    def upsert(self, bars: list[Bar]) -> int:
        if not bars:
            return 0
        with self.db.session() as s:
            keys = {(b.symbol, b.day) for b in bars}
            symbols = {k[0] for k in keys}
            days = {k[1] for k in keys}
            existing = {
                (r.symbol, r.day): r
                for r in s.scalars(
                    select(DailyPrice).where(DailyPrice.symbol.in_(symbols), DailyPrice.day.in_(days))
                )
            }
            for b in bars:
                row = existing.get((b.symbol, b.day))
                if row is None:
                    row = DailyPrice(symbol=b.symbol, day=b.day)
                    s.add(row)
                    existing[(b.symbol, b.day)] = row
                for f in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "value",
                    "market_cap",
                    "listed_shares",
                    "name",
                    "market",
                ):
                    setattr(row, f, getattr(b, f))
        return len(bars)

    async def sync_day(self, day: date) -> int:
        """기준일 전 종목 일봉을 받아 저장. 이미 있으면 건너뛴다."""
        with self.db.session() as s:
            n = s.scalar(select(func.count()).select_from(DailyPrice).where(DailyPrice.day == day)) or 0
        if n > 0:
            return 0
        bars = await self.provider.fetch_day(day)
        return self.upsert(bars)

    async def ensure_history(self, symbol: str, until: date, days: int = 180) -> None:
        start = until - timedelta(days=days)
        with self.db.session() as s:
            n = (
                s.scalar(
                    select(func.count())
                    .select_from(DailyPrice)
                    .where(DailyPrice.symbol == symbol, DailyPrice.day >= start, DailyPrice.day <= until)
                )
                or 0
            )
        # 거래일 비율(~0.68)을 감안해 70% 이상 있으면 충분하다고 본다
        if n >= days * 0.68 * 0.7:
            return
        try:
            self.upsert(await self.provider.fetch_history(symbol, start, until))
        except Exception as e:  # 시세 일부 실패는 판단을 막지 않는다
            log.warning("과거 시세 수집 실패 %s: %s", symbol, e)

    # ------------------------------------------------------------- 읽기
    def latest_day(self) -> date | None:
        with self.db.session() as s:
            return s.scalar(select(func.max(DailyPrice.day)))

    def history(self, symbol: str, until: date | None = None, n: int = 120) -> list[Bar]:
        with self.db.session() as s:
            q = select(DailyPrice).where(DailyPrice.symbol == symbol)
            if until is not None:
                q = q.where(DailyPrice.day <= until)
            rows = list(s.scalars(q.order_by(DailyPrice.day.desc()).limit(n)))
        return [_to_bar(r) for r in reversed(rows)]

    def last_bar(self, symbol: str, until: date | None = None) -> Bar | None:
        h = self.history(symbol, until, 1)
        return h[-1] if h else None

    def bar_on(self, symbol: str, day: date) -> Bar | None:
        with self.db.session() as s:
            r = s.scalar(select(DailyPrice).where(DailyPrice.symbol == symbol, DailyPrice.day == day))
            return _to_bar(r) if r else None

    def snapshot(self, day: date | None = None) -> list[Bar]:
        day = day or self.latest_day()
        if day is None:
            return []
        with self.db.session() as s:
            return [_to_bar(r) for r in s.scalars(select(DailyPrice).where(DailyPrice.day == day))]

    def name_of(self, symbol: str) -> str:
        b = self.last_bar(symbol)
        return b.name if b else ""
