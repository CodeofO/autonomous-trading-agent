"""주요 지표 띠 (대시보드 상단): 코스피·코스닥·나스닥100·S&P500·원/달러·국고채 10년 (D-40).

토스 Open API 의 시장 지표(`/market-indicators`)는 코스피·코스닥·한국 국채(KR_BOND_*)만 제공한다.
미국 지수는 없어서 지수를 따라가는 ETF(QQQ·SPY) 시세로 대신하고, 화면에 'QQQ ETF' 처럼 표시한다.
전일 대비는 일봉의 바로 전 거래일 종가(최신순 두 번째 봉)와 비교한다.
현재가(지수·ETF·환율)는 15초, 일봉(전일 종가·추이선)은 10분 캐시 — 장이 닫혀 있어도 시간외·데이마켓·환율
움직임이 화면에 바로 보이게 하면서 호출 수는 묶는다 (D-61).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from app.core.clock import Clock
from app.market.toss import TossClient

log = logging.getLogger(__name__)

# (키, 이름, 종류, 단위, 비고)
INDICATORS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("KOSPI", "코스피", "index", "pt", None),
    ("KOSDAQ", "코스닥", "index", "pt", None),
    ("QQQ", "나스닥100", "etf", "$", "QQQ ETF"),
    ("SPY", "S&P500", "etf", "$", "SPY ETF"),
    ("DIA", "다우", "etf", "$", "DIA ETF"),  # 다우존스 지수도 토스에 없어 지수를 따라가는 ETF 로 (D-72)
    ("USDKRW", "원/달러", "fx", "원", None),
    ("KR_BOND_10Y", "국고채 10년", "bond", "%", None),
)
SPARK_DAYS = 30
TTL_SEC = 15.0  # 현재가
CANDLE_TTL_SEC = 600.0  # 일봉 (하루에 한 번 바뀐다)


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _closes(candles: list[dict[str, Any]]) -> list[float]:
    """최신순 일봉 → 오래된 순 종가."""
    return [c for c in (_f(x.get("closePrice")) for x in reversed(candles)) if c is not None]


def _row(
    key: str, name: str, kind: str, unit: str, note: str | None, price: float | None, closes: list[float]
) -> dict[str, Any]:
    prev = closes[-2] if len(closes) >= 2 else None
    spark = list(closes)
    if price is not None and spark:
        spark[-1] = price  # 장중·시간외 현재가를 마지막 점으로
    change = round(price - prev, 4) if price is not None and prev is not None else None
    pct = round(change / prev * 100, 2) if change is not None and prev and kind != "bond" else None
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "unit": unit,
        "note": note,
        "price": price,
        "prev_close": prev,
        "change": change,  # 국채는 %p
        "change_pct": pct,
        "direction": None,
        "spark": spark,
        "error": price is None,
    }


class MarketIndicators:
    def __init__(
        self, client: TossClient, clock: Clock, ttl: float = TTL_SEC, candle_ttl: float = CANDLE_TTL_SEC
    ) -> None:
        self.client = client
        self.clock = clock
        self.ttl = ttl
        self.candle_ttl = candle_ttl
        self._cache: dict[str, Any] | None = None
        self._at = 0.0
        self._candles: dict[str, list[float]] = {}
        self._candles_at = 0.0
        self._lock = asyncio.Lock()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            if self._cache is None or time.monotonic() - self._at > self.ttl:
                self._cache = {"as_of": self.clock.now().isoformat(), "items": await self._fetch()}
                self._at = time.monotonic()
            return self._cache

    async def _safe(self, what: str, fn: Callable[[], Awaitable[Any]], default: Any) -> Any:
        try:
            return await fn()
        except Exception as e:  # 한 지표가 실패해도 나머지는 보여 준다
            log.warning("주요 지표 조회 실패 (%s): %s", what, type(e).__name__)
            return default

    async def _closes(self) -> dict[str, list[float]]:
        """지표별 일봉 종가(오래된 순). 10분 캐시 — 받지 못한 지표만 다음 현재가 갱신 때 다시 받는다."""
        expired = time.monotonic() - self._candles_at > self.candle_ttl
        tc = self.client
        got: dict[str, list[float]] = {}
        for key, _name, kind, _unit, _note in INDICATORS:
            if kind == "fx" or (not expired and key in self._candles):
                continue
            rows: list[dict[str, Any]] | None
            if kind == "etf":
                res = await self._safe(f"{key} 일봉", partial(tc.candles, key, "1d", SPARK_DAYS), None)
                rows = res[0] if res is not None else None
            else:
                rows = await self._safe(f"{key} 일봉", partial(tc.index_candles, key, SPARK_DAYS), None)
            if rows is not None:
                got[key] = _closes(rows)
        self._candles.update(got)  # 다시 받다 실패한 지표는 직전 일봉을 그대로 쓴다
        if expired:
            self._candles_at = time.monotonic()
        return self._candles

    async def _fetch(self) -> list[dict[str, Any]]:
        tc = self.client
        kr = [k for k, _, kind, _, _ in INDICATORS if kind in ("index", "bond")]
        us = [k for k, _, kind, _, _ in INDICATORS if kind == "etf"]
        idx_px = await self._safe("지수 현재가", partial(tc.index_prices, tuple(kr)), [])
        us_px = await self._safe("미국 ETF 현재가", partial(tc.prices, us), {})
        fx = await self._safe("환율", tc.exchange_rate, {})
        closes = await self._closes()
        last = {str(r.get("symbol")): _f(r.get("lastPrice")) for r in idx_px}
        out: list[dict[str, Any]] = []
        for key, name, kind, unit, note in INDICATORS:
            if kind in ("index", "bond"):
                out.append(_row(key, name, kind, unit, note, last.get(key), closes.get(key, [])))
            elif kind == "etf":
                px = us_px.get(key)
                out.append(_row(key, name, kind, unit, note, px[0] if px else None, closes.get(key, [])))
            else:  # 환율: 토스는 현재 환율과 방향(UP/DOWN)만 준다
                row = _row(key, name, kind, unit, note, _f(fx.get("rate")), [])
                d = str(fx.get("rateChangeType") or "").upper()
                row["direction"] = "up" if d == "UP" else "down" if d == "DOWN" else None
                out.append(row)
        return out
