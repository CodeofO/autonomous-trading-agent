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
from app.core.us_calendar import NY, UsMarketCalendar
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


@dataclass(frozen=True)
class FakeUsStock:
    symbol: str
    name: str
    english: str
    market: str  # NASDAQ | NYSE | AMEX
    type: str  # STOCK | ETF
    base: float  # 달러
    trend: float
    shares: float
    volume: float
    phase: float


# 미장 (D-48): 가격은 달러, 일봉 날짜는 뉴욕 거래일
US_STOCKS = [
    FakeUsStock("AAPL", "애플", "APPLE INC", "NASDAQ", "STOCK", 190.0, 0.002, 1.47e10, 5e7, 0.0),
    FakeUsStock(
        "MSFT", "마이크로소프트", "MICROSOFT CORP", "NASDAQ", "STOCK", 410.0, 0.001, 7.43e9, 2e7, 1.0
    ),
    FakeUsStock("NVDA", "엔비디아", "NVIDIA CORP", "NASDAQ", "STOCK", 130.0, 0.004, 2.44e10, 2e8, 2.0),
    FakeUsStock("AMZN", "아마존닷컴", "AMAZON.COM INC", "NASDAQ", "STOCK", 180.0, 0.001, 1.05e10, 4e7, 3.0),
    FakeUsStock("GOOGL", "알파벳 A", "ALPHABET INC-CL A", "NASDAQ", "STOCK", 165.0, 0.001, 5.8e9, 3e7, 4.0),
    FakeUsStock(
        "META", "메타 플랫폼스", "META PLATFORMS INC", "NASDAQ", "STOCK", 560.0, 0.0, 2.5e9, 1.5e7, 5.0
    ),
    FakeUsStock("TSLA", "테슬라", "TESLA INC", "NASDAQ", "STOCK", 250.0, -0.002, 3.2e9, 9e7, 6.0),
    FakeUsStock("KO", "코카콜라", "COCA-COLA CO", "NYSE", "STOCK", 62.0, 0.0005, 4.3e9, 1.4e7, 7.0),
    FakeUsStock("QQQ", "인베스코 QQQ", "INVESCO QQQ TRUST", "NASDAQ", "ETF", 480.0, 0.001, 6e8, 4e7, 8.0),
    FakeUsStock("SPY", "SPDR S&P500", "SPDR S&P 500 ETF", "AMEX", "ETF", 560.0, 0.0008, 9e8, 6e7, 9.0),
    FakeUsStock("DIA", "SPDR 다우존스", "SPDR DOW JONES ETF", "AMEX", "ETF", 420.0, 0.0005, 3e8, 3e6, 10.0),
    FakeUsStock("PENY", "페니주", "PENNY CORP", "NASDAQ", "STOCK", 0.5234, 0.0, 1e8, 5e6, 11.0),
]
US_BY = {s.symbol: s for s in US_STOCKS}


class FakeTossClient:
    def __init__(self, clock: Clock, calendar: MarketCalendar | None = None) -> None:
        self.clock = clock
        self.cal = calendar or MarketCalendar()
        self.overrides: dict[str, float] = {}  # 실시간 현재가 덮어쓰기
        self.fail: TossApiError | None = None  # 설정하면 모든 호출이 이 오류
        self.calls: list[str] = []
        self._days = self.cal.trading_days(START, date(2027, 12, 31))
        self._idx = {d: i for i, d in enumerate(self._days)}
        self.us_cal = UsMarketCalendar()
        self._us_days = self.us_cal.trading_days(START, date(2027, 12, 31))
        self._us_idx = {d: i for i, d in enumerate(self._us_days)}
        self.us_calendar: dict[str, Any] = {}  # market_calendar_us 응답 (비우면 내장 캘린더 사용)

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

    # ------------------------------------------------------------ 미장
    def _us_today(self) -> date:
        return self.clock.now().astimezone(NY).date()

    def _us_close(self, s: FakeUsStock, d: date) -> float:
        i = self._us_idx.get(d, 0)
        k = max(0, i - self._us_idx.get(ANCHOR, 0))
        px = s.base * (1 + s.trend) ** k * (1 + 0.02 * math.sin(i / 5 + s.phase))
        return round(px, 4) if px < 1 else round(px, 2)

    def _us_last(self, s: FakeUsStock) -> float:
        days = [d for d in self._us_days if d <= self._us_today()]
        return self._us_close(s, days[-1])

    def _us_change(self, s: FakeUsStock) -> float:
        days = [d for d in self._us_days if d <= self._us_today()]
        return self.last_price(s.symbol) / self._us_close(s, days[-2]) - 1

    def last_price(self, symbol: str) -> float:
        if symbol in self.overrides:
            return self.overrides[symbol]
        if symbol in US_BY:
            return self._us_last(US_BY[symbol])
        s = BY[symbol]
        days = [d for d in self._days if d <= self._today()]
        return self._close(s, days[-1])

    # ------------------------------------------------------------ TossClient 인터페이스
    async def prices(self, symbols: list[str]) -> dict[str, tuple[float, datetime | None]]:
        self._check("prices")
        return {s: (self.last_price(s), self.clock.now()) for s in symbols if s in BY or s in US_BY}

    async def candles(
        self, symbol: str, interval: str = "1d", count: int = 200, before: datetime | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        self._check("candles")
        if symbol in US_BY:
            return self._us_candles(US_BY[symbol], count, before)
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

    def _us_candles(
        self, s: FakeUsStock, count: int, before: datetime | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """미국 일봉: 1d 타임스탬프는 뉴욕 자정을 KST 로 표기 (예: EST 일 때 T14:00:00+09:00)."""

        def ts(d: date) -> datetime:
            return datetime.combine(d, time(0), NY)

        days = [d for d in self._us_days if d <= self._us_today() and (before is None or ts(d) < before)]
        days = days[-count:][::-1]
        rows = []
        for d in days:
            c = self._us_close(s, d)
            prev = self._us_close(s, self._us_days[max(0, self._us_idx[d] - 1)])
            rows.append(
                {
                    "timestamp": ts(d).astimezone(KST).isoformat(),
                    "openPrice": str(prev),
                    "highPrice": str(round(max(prev, c) * 1.01, 4)),
                    "lowPrice": str(round(min(prev, c) * 0.99, 4)),
                    "closePrice": str(c),
                    "volume": str(int(s.volume)),
                    "currency": "USD",
                }
            )
        nxt = ts(days[-1]).astimezone(KST).isoformat() if days and self._us_idx[days[-1]] > 0 else None
        return rows, nxt

    def _us_info(self, s: FakeUsStock) -> dict[str, Any]:
        return {
            "symbol": s.symbol,
            "name": s.name,
            "englishName": s.english,
            "isinCode": f"US000{s.symbol}",
            "market": s.market,
            "securityType": s.type,
            "isCommonShare": True,
            "status": "ACTIVE",
            "currency": "USD",
            "listDate": "1990-01-02",
            "delistDate": None,
            "sharesOutstanding": str(int(s.shares)),
            "leverageFactor": "1" if s.type == "ETF" else None,
            "koreanMarketDetail": None,
        }

    async def stocks(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self._check("stocks")
        us = {x: self._us_info(US_BY[x]) for x in symbols if x in US_BY}
        return us | {
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

    async def stocks_all(self, market: str, security_type: str | None = "STOCK") -> list[dict[str, Any]]:
        self._check("stocks_all")
        if market in ("NASDAQ", "NYSE", "AMEX", "US_ETC"):
            return [
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "englishName": s.english,
                    "securityType": s.type,
                    "isCommonShare": True,
                    "isinCode": f"US000{s.symbol}",
                }
                for s in US_STOCKS
                if s.market == market and (security_type is None or s.type == security_type)
            ]
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

    async def rankings(
        self, type_: str, duration: str, count: int = 100, market_country: str = "KR"
    ) -> list[dict[str, Any]]:
        self._check("rankings")
        if type_ in ("TOP_GAINERS", "TOP_LOSERS") and duration == "realtime":
            raise TossApiError(400, "unsupported-ranking-duration", "realtime 미지원")
        if market_country == "US":
            rows = [
                {
                    "symbol": s.symbol,
                    "currency": "USD",
                    "price": {
                        "lastPrice": str(self.last_price(s.symbol)),
                        "basePrice": "0",
                        "changeRate": f"{self._us_change(s):.4f}",
                    },
                    "tradingVolume": str(int(s.volume)),
                    "tradingAmount": str(int(s.volume * self.last_price(s.symbol))),
                }
                for s in US_STOCKS
            ]
        else:
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

    async def market_calendar_us(self, day: date | None = None) -> dict[str, Any]:
        self._check("calendar_us")
        return dict(self.us_calendar)  # 비어 있으면 내장 NYSE 캘린더 사용

    async def orderbook(self, symbol: str) -> dict[str, Any]:
        self._check("orderbook")
        p = self.last_price(symbol)
        if symbol in US_BY:
            return {
                "timestamp": self.clock.now().isoformat(),
                "currency": "USD",
                "asks": [{"price": f"{p + 0.01 * i:.2f}", "volume": str(100 * i)} for i in range(1, 6)],
                "bids": [{"price": f"{p - 0.01 * (i - 1):.2f}", "volume": str(150 * i)} for i in range(1, 6)],
            }
        return {
            "timestamp": self.clock.now().isoformat(),
            "currency": "KRW",
            "asks": [{"price": str(p + 100 * i), "volume": str(1000 * i)} for i in range(1, 6)],
            "bids": [{"price": str(p - 100 * (i - 1)), "volume": str(1500 * i)} for i in range(1, 6)],
        }

    async def trades(self, symbol: str, count: int = 50) -> list[dict[str, Any]]:
        self._check("trades")
        p = self.last_price(symbol)
        cur = "USD" if symbol in US_BY else "KRW"
        return [
            {"price": str(p), "volume": "100", "timestamp": self.clock.now().isoformat(), "currency": cur}
        ] * 5

    async def price_limits(self, symbol: str) -> dict[str, Any]:
        self._check("price_limits")
        if symbol in US_BY:  # 미국 주식: 가격제한폭 없음 → null
            return {"upperLimitPrice": None, "lowerLimitPrice": None, "currency": "USD"}
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

    async def sector(self, sector_id: str, market_country: str = "KR") -> dict[str, Any]:
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

    async def sector_stocks(self, sector_id: str, market_country: str = "KR") -> list[dict[str, Any]]:
        self._check("sector_stocks")
        if market_country == "US":
            return [
                {
                    "symbol": s.symbol,
                    "name": s.name,
                    "volume": str(int(s.volume)),
                    "tradingAmount": str(int(s.volume * self.last_price(s.symbol))),
                    "marketCapitalization": str(int(s.shares * self.last_price(s.symbol))),
                    "currency": "USD",
                }
                for s in US_STOCKS[:3]
            ]
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

    async def sector_rankings(
        self, type_: str = "TOP_GAINERS", duration: str = "1d", market_country: str = "KR"
    ) -> list[dict[str, Any]]:
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
