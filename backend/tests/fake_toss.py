"""테스트 전용 가짜 토스증권 클라이언트 (앱 코드에는 없음).

`app.market.toss.TossClient` 와 같은 메서드·같은 응답 모양(공식 OpenAPI 1.2.28 예시 형태)을 돌려준다.
가격은 결정적인 수식으로 만든다: 기준일(2026-08-03) 이후 종목별 추세 + 작은 사인파.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from app.core.clock import KST, Clock
from app.core.market_calendar import MarketCalendar
from app.market.toss import TossApiError

ANCHOR = date(2026, 8, 3)
START = date(2025, 6, 2)


@dataclass(frozen=True)
class FakeStock:
    symbol: str
    name: str
    market: str
    base: float
    trend: float  # 기준일 이후 일 수익률
    shares: float
    volume: float
    phase: float


STOCKS = [
    FakeStock("005930", "삼성전자", "KOSPI", 70_000, 0.004, 5.9e9, 15e6, 0.0),
    FakeStock("000660", "SK하이닉스", "KOSPI", 180_000, 0.003, 7.28e8, 4e6, 1.0),
    FakeStock("035420", "NAVER", "KOSPI", 200_000, 0.0, 1.58e8, 1e6, 2.0),
    FakeStock("005380", "현대차", "KOSPI", 230_000, -0.004, 2.09e8, 8e5, 3.0),
    FakeStock("068270", "셀트리온", "KOSPI", 170_000, 0.0005, 2.1e8, 9e5, 4.0),
    FakeStock("247540", "에코프로비엠", "KOSDAQ", 150_000, 0.001, 9.78e7, 1.2e6, 5.0),
]
BY = {s.symbol: s for s in STOCKS}


class FakeTossClient:
    def __init__(self, clock: Clock, calendar: MarketCalendar | None = None) -> None:
        self.clock = clock
        self.cal = calendar or MarketCalendar()
        self.overrides: dict[str, float] = {}  # 실시간 현재가 덮어쓰기
        self.fail: TossApiError | None = None  # 설정하면 모든 호출이 이 오류
        self.calls: list[str] = []
        self._days = self.cal.trading_days(START, date(2027, 12, 31))
        self._idx = {d: i for i, d in enumerate(self._days)}

    # ------------------------------------------------------------ 내부
    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    def _close(self, s: FakeStock, d: date) -> float:
        i = self._idx.get(d, 0)
        k = max(0, i - self._idx.get(ANCHOR, 0))
        px = s.base * (1 + s.trend) ** k * (1 + 0.02 * math.sin(i / 5 + s.phase))
        return float(round(px / 100) * 100)

    def _today(self) -> date:
        return self.clock.now().astimezone(KST).date()

    def last_price(self, symbol: str) -> float:
        if symbol in self.overrides:
            return self.overrides[symbol]
        s = BY[symbol]
        days = [d for d in self._days if d <= self._today()]
        return self._close(s, days[-1])

    # ------------------------------------------------------------ TossClient 인터페이스
    async def prices(self, symbols: list[str]) -> dict[str, tuple[float, datetime | None]]:
        self._check("prices")
        return {s: (self.last_price(s), self.clock.now()) for s in symbols if s in BY}

    async def candles(
        self, symbol: str, interval: str = "1d", count: int = 200, before: datetime | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        self._check("candles")
        s = BY.get(symbol)
        if s is None:
            raise TossApiError(404, "stock-not-found", symbol)
        limit = min(self._today(), (before.astimezone(KST).date() if before else self._today()))
        days = [d for d in self._days if d <= limit][-count:][::-1]  # 최신순
        rows = []
        for d in days:
            c = self._close(s, d)
            prev = self._close(s, self._days[max(0, self._idx[d] - 1)])
            rows.append(
                {
                    "timestamp": datetime.combine(d, time(0), KST).isoformat(),
                    "openPrice": str(prev),
                    "highPrice": str(max(prev, c) * 1.01),
                    "lowPrice": str(min(prev, c) * 0.99),
                    "closePrice": str(c),
                    "volume": str(int(s.volume)),
                    "currency": "KRW",
                }
            )
        nxt = None
        if days and self._idx[days[-1]] > 0:
            nxt = datetime.combine(self._days[self._idx[days[-1]] - 1], time(0), KST).isoformat()
        return rows, nxt

    async def stocks(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self._check("stocks")
        return {
            s.symbol: {
                "symbol": s.symbol,
                "name": s.name,
                "englishName": s.name,
                "isinCode": f"KR7{s.symbol}003",
                "market": s.market,
                "securityType": "STOCK",
                "isCommonShare": True,
                "status": "ACTIVE",
                "currency": "KRW",
                "listDate": "2000-01-04",
                "sharesOutstanding": str(int(s.shares)),
                "koreanMarketDetail": {
                    "liquidationTrading": False,
                    "nxtSupported": True,
                    "krxTradingSuspended": False,
                    "nxtTradingSuspended": False,
                },
            }
            for s in (BY[x] for x in symbols if x in BY)
        }

    async def stocks_all(self, market: str) -> list[dict[str, Any]]:
        self._check("stocks_all")
        return [
            {
                "symbol": s.symbol,
                "name": s.name,
                "securityType": "STOCK",
                "isCommonShare": True,
                "isinCode": f"KR7{s.symbol}003",
            }
            for s in STOCKS
            if s.market == market
        ]

    def _change(self, s: FakeStock) -> float:
        days = [d for d in self._days if d <= self._today()]
        prev = self._close(s, days[-2])
        return self.last_price(s.symbol) / prev - 1

    async def rankings(self, type_: str, duration: str, count: int = 100) -> list[dict[str, Any]]:
        self._check("rankings")
        rows = [
            {
                "symbol": s.symbol,
                "currency": "KRW",
                "price": {
                    "lastPrice": str(self.last_price(s.symbol)),
                    "basePrice": "0",
                    "changeRate": f"{self._change(s):.4f}",
                },
                "tradingVolume": str(int(s.volume)),
                "tradingAmount": str(int(s.volume * self.last_price(s.symbol))),
            }
            for s in STOCKS
        ]
        if type_ == "TOP_GAINERS":
            rows.sort(key=lambda r: -float(r["price"]["changeRate"]))
        elif type_ == "TOP_LOSERS":
            rows.sort(key=lambda r: float(r["price"]["changeRate"]))
        else:
            rows.sort(key=lambda r: -float(r["tradingAmount"]))
        return [{"rank": i + 1, **r} for i, r in enumerate(rows[:count])]

    async def market_calendar_kr(self, day: date | None = None) -> dict[str, Any]:
        self._check("calendar")
        return {}  # 내장 캘린더 사용

    async def orderbook(self, symbol: str) -> dict[str, Any]:
        self._check("orderbook")
        p = self.last_price(symbol)
        return {
            "timestamp": self.clock.now().isoformat(),
            "currency": "KRW",
            "asks": [{"price": str(p + 100 * i), "volume": str(1000 * i)} for i in range(1, 6)],
            "bids": [{"price": str(p - 100 * (i - 1)), "volume": str(1500 * i)} for i in range(1, 6)],
        }

    async def trades(self, symbol: str, count: int = 50) -> list[dict[str, Any]]:
        self._check("trades")
        p = self.last_price(symbol)
        return [
            {"price": str(p), "volume": "100", "timestamp": self.clock.now().isoformat(), "currency": "KRW"}
        ] * 5

    async def price_limits(self, symbol: str) -> dict[str, Any]:
        self._check("price_limits")
        p = self.last_price(symbol)
        return {"upperLimitPrice": str(p * 1.3), "lowerLimitPrice": str(p * 0.7), "currency": "KRW"}

    async def warnings(self, symbol: str) -> list[dict[str, Any]]:
        self._check("warnings")
        if symbol == "247540":
            return [
                {"warningType": "OVERHEATED", "exchange": "KRX", "startDate": "2026-10-28", "endDate": None}
            ]
        return []

    async def stock_trend(self, symbol: str, kind: str, count: int = 10) -> list[dict[str, Any]]:
        self._check(kind)
        sign = 1 if BY[symbol].trend >= 0 else -1
        days = [d for d in self._days if d < self._today()][-count:][::-1]
        out = []
        for i, d in enumerate(days):
            if kind == "investor-trading":
                out.append(
                    {
                        "date": d.isoformat(),
                        "foreigner": {
                            "buyVolume": "1000",
                            "sellVolume": "500",
                            "netBuyVolume": str(sign * (500 + i)),
                        },
                        "institution": {"buyVolume": "800", "sellVolume": "900", "netBuyVolume": "-100"},
                        "individual": {
                            "buyVolume": "500",
                            "sellVolume": "900",
                            "netBuyVolume": str(-sign * 400),
                        },
                    }
                )
            elif kind == "program-trades":
                out.append(
                    {
                        "date": d.isoformat(),
                        "arbitrage": {"netBuyVolume": "10"},
                        "nonArbitrage": {"netBuyVolume": str(sign * 200)},
                    }
                )
            elif kind == "short-selling":
                out.append({"date": d.isoformat(), "shortSellingVolumeRate": "0.031"})
            elif kind == "credit-trades":
                out.append({"date": d.isoformat(), "marginLoan": {"balanceRate": "0.0042"}})
            else:
                out.append({"date": d.isoformat(), "balanceQuantity": str(15_000_000 - i * 1000)})
        return out

    async def exchange_rate(self) -> dict[str, Any]:
        self._check("fx")
        return {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1380.5", "rateChangeType": "UP"}

    async def index_prices(self, symbols: tuple[str, ...] = ("KOSPI", "KOSDAQ")) -> list[dict[str, Any]]:
        self._check("index_prices")
        return [{"symbol": "KOSPI", "lastPrice": "2812.45"}, {"symbol": "KOSDAQ", "lastPrice": "845.32"}]

    async def index_candles(self, symbol: str, count: int = 60) -> list[dict[str, Any]]:
        self._check("index_candles")
        days = [d for d in self._days if d <= self._today()][-count:][::-1]
        base = 2800.0 if symbol == "KOSPI" else 840.0
        return [
            {
                "timestamp": datetime.combine(d, time(9), KST).isoformat(),
                "closePrice": str(base * (1 + 0.0005 * (len(days) - i))),
            }
            for i, d in enumerate(days)
        ]

    async def index_investor_trading(self, symbol: str, count: int = 5) -> list[dict[str, Any]]:
        self._check("index_investor")
        return [
            {
                "date": "2026-10-30",
                "individual": {"buyAmount": "5200000000000", "sellAmount": "5350000000000"},
                "foreigner": {"buyAmount": "3800000000000", "sellAmount": "3600000000000"},
                "institution": {"buyAmount": "2100000000000", "sellAmount": "2180000000000"},
            }
        ][:count]

    async def sectors(self) -> list[dict[str, Any]]:
        self._check("sectors")
        return [
            {"sectorId": "10", "name": "IT 하드웨어", "depth": 0, "parentSectorId": None},
            {"sectorId": "100", "name": "반도체", "depth": 1, "parentSectorId": "10"},
            {"sectorId": "200", "name": "자동차", "depth": 0, "parentSectorId": None},
        ]

    async def sector(self, sector_id: str) -> dict[str, Any]:
        self._check("sector")
        return {
            "sectorId": sector_id,
            "name": "반도체",
            "summary": "반도체 설계·제조 전문 회사들",
            "performance": {
                "changeRates": [
                    {"duration": "1d", "changeRate": "0.0179"},
                    {"duration": "1mo", "changeRate": "-0.0231"},
                ]
            },
        }

    async def sector_stocks(self, sector_id: str) -> list[dict[str, Any]]:
        self._check("sector_stocks")
        return [
            {
                "symbol": s.symbol,
                "name": s.name,
                "volume": str(int(s.volume)),
                "tradingAmount": str(int(s.volume * self.last_price(s.symbol))),
                "marketCapitalization": str(int(s.shares * self.last_price(s.symbol))),
                "currency": "KRW",
            }
            for s in STOCKS[:2]
        ]

    async def sector_rankings(self, type_: str = "TOP_GAINERS", duration: str = "1d") -> list[dict[str, Any]]:
        self._check("sector_rankings")
        return [
            {"rank": 1, "sectorId": "100", "name": "반도체", "changeRate": "0.0179"},
            {"rank": 2, "sectorId": "200", "name": "자동차", "changeRate": "-0.004"},
        ]

    async def investor_rankings(self, count: int = 20) -> dict[str, Any]:
        self._check("investor_rankings")
        buy = [
            {"rank": 1, "symbol": "005930", "netBuyAmount": "386487682000"},
            {"rank": 2, "symbol": "000660", "netBuyAmount": "43100000000"},
        ]
        sell = [{"rank": 1, "symbol": "005380", "netSellAmount": "91201805000"}]
        return {
            "foreigner": {"netBuyRankings": buy, "netSellRankings": sell},
            "institution": {"netBuyRankings": buy[::-1], "netSellRankings": sell},
        }

    async def aclose(self) -> None:
        return None


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), KST)
