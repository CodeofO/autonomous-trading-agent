from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    value: float = 0.0
    market_cap: float = 0.0
    listed_shares: float = 0.0
    name: str = ""
    market: str = ""


class MarketDataProvider(Protocol):
    name: str

    async def fetch_day(self, day: date) -> list[Bar]:
        """해당 기준일의 전 종목 일봉. 아직 공개 전이면 빈 리스트."""
        ...

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        """한 종목의 [start, end] 일봉 (오름차순)."""
        ...
