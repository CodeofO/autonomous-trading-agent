"""차트 봉 (D-63): 증권사 차트처럼 분·일 단위를 골라 본다 — 종목과 주요 지표(지수·국채·미국 지수 ETF).

토스 Open API 의 봉 단위는 `1m`·`1d` 두 가지뿐이다 (공식 OpenAPI 1.2.28 `interval` enum, 한 번에 최대 200개,
`before`/`nextBefore` 로 이전 페이지). 그래서
- 1분봉은 그대로, 5·15·30·60분봉은 1분봉을 모아서,
- 일봉은 그대로, 주봉(월요일 시작)·월봉은 일봉을 모아서 만든다.

봉 시각 규칙 (공식 명세):
- 종목 `GET /api/v1/candles`: 1분봉 timestamp = **봉 종료 시각** (`[timestamp-1분, timestamp)` 집계), 일봉 = 거래일 현지 자정.
- 지표 `GET /api/v1/market-indicators/{symbol}/candles`: timestamp = **봉 시작 시각**. 분봉은 KOSPI·KOSDAQ 만,
  국채(KR_BOND_*)는 일봉만. 환율 봉은 Open API 에 없다.

분 단위 봉의 시각은 한국 시각 벽시계로 돌려준다(차트 라이브러리는 UTC 로 그리므로 한국 시각을 UTC 인 것처럼 넣은
초 단위 숫자). 일·주·월 봉은 그 시장 현지 날짜 "YYYY-MM-DD".
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.clock import KST

Page = tuple[list[dict[str, Any]], str | None]
Fetch = Callable[[datetime | None], Awaitable[Page]]


@dataclass(frozen=True)
class Interval:
    key: str
    label: str
    base: str  # 토스 봉 단위 1m | 1d
    unit: str  # min | day | week | month
    minutes: int  # 분 단위 봉 크기 (분봉만)
    base_count: int  # 받아 올 원래 봉 수 (200개씩 여러 번)


INTERVALS: tuple[Interval, ...] = (
    Interval("1m", "1분", "1m", "min", 1, 200),
    Interval("5m", "5분", "1m", "min", 5, 600),
    Interval("15m", "15분", "1m", "min", 15, 1200),
    Interval("30m", "30분", "1m", "min", 30, 1600),
    Interval("60m", "1시간", "1m", "min", 60, 2000),
    Interval("1d", "일", "1d", "day", 0, 200),
    Interval("1w", "주", "1d", "week", 0, 600),
    Interval("1M", "월", "1d", "month", 0, 1200),
)
BY_KEY = {iv.key: iv for iv in INTERVALS}
PAGE = 200  # 토스 한 번 최대
MAX_PAGES = 10  # 한 차트에 이 이상은 부르지 않는다 (지표 차트 한도 초당 4회)
TTL_MINUTE = 20.0  # 분봉 차트 캐시(초)
TTL_DAILY = 300.0


class ChartError(ValueError):
    """화면에 그대로 보여 줄 한국어 사유 (API 400/404)."""


def get(key: str) -> Interval:
    iv = BY_KEY.get(key)
    if iv is None:
        raise ChartError(f"고를 수 없는 봉 단위예요 ({', '.join(BY_KEY)})")
    return iv


def catalog(*, minutes: bool = True) -> list[dict[str, Any]]:
    """화면의 봉 단위 버튼. minutes=False(국채)면 분 단위는 고를 수 없다."""
    return [
        {"key": iv.key, "label": iv.label, "intraday": iv.base == "1m", "enabled": minutes or iv.base == "1d"}
        for iv in INTERVALS
    ]


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN 제외


@dataclass
class _Raw:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse(candles: list[dict[str, Any]], *, end_stamped: bool) -> list[_Raw]:
    """토스 봉(최신순, 값은 문자열) → 오래된 순. end_stamped: 종목 1분봉처럼 timestamp 가 봉 종료 시각."""
    out: list[_Raw] = []
    for c in candles:
        ts, close = c.get("timestamp"), _f(c.get("closePrice"))
        if not ts or close is None or close <= 0:
            continue
        start = datetime.fromisoformat(str(ts))
        if end_stamped:
            start -= timedelta(minutes=1)
        o, h, lo = _f(c.get("openPrice")), _f(c.get("highPrice")), _f(c.get("lowPrice"))
        out.append(
            _Raw(
                start,
                o if o and o > 0 else close,
                max(h, close) if h and h > 0 else close,
                min(lo, close) if lo and lo > 0 else close,
                close,
                _f(c.get("volume")) or 0.0,
            )
        )
    out.sort(key=lambda r: r.start)
    return out


def bucket(t: datetime, iv: Interval, tz: ZoneInfo) -> int | str:
    """봉 하나가 들어갈 칸: 분 단위 = 한국 시각 벽시계 초(자정 기준 격자), 일·주·월 = 현지 날짜 문자열."""
    if iv.base == "1m":
        local = t.astimezone(KST)
        minute = (local.hour * 60 + local.minute) // iv.minutes * iv.minutes
        wall = datetime(local.year, local.month, local.day, minute // 60, minute % 60, tzinfo=UTC)
        return int(wall.timestamp())
    d: date = t.astimezone(tz).date()
    if iv.unit == "week":
        d -= timedelta(days=d.weekday())
    elif iv.unit == "month":
        d = d.replace(day=1)
    return d.isoformat()


def aggregate(raws: list[_Raw], iv: Interval, tz: ZoneInfo) -> list[dict[str, Any]]:
    bars: dict[int | str, dict[str, Any]] = {}
    for r in raws:
        k = bucket(r.start, iv, tz)
        b = bars.get(k)
        if b is None:
            bars[k] = {
                "time": k,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
        else:
            b["high"] = max(b["high"], r.high)
            b["low"] = min(b["low"], r.low)
            b["close"] = r.close
            b["volume"] += r.volume
    return list(bars.values())  # raws 가 오래된 순이라 칸도 오래된 순


async def collect(fetch: Fetch, need: int) -> list[dict[str, Any]]:
    """최신부터 need 개(또는 MAX_PAGES 페이지)까지 이전 페이지를 이어 받는다."""
    out: list[dict[str, Any]] = []
    before: datetime | None = None
    for _ in range(MAX_PAGES):
        rows, nxt = await fetch(before)
        out += rows
        if len(out) >= need or not nxt or not rows:
            break
        before = datetime.fromisoformat(nxt)
    return out


_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}


async def bars(
    key: tuple[Any, ...], iv: Interval, tz: ZoneInfo, fetch: Fetch, *, end_stamped: bool
) -> list[dict[str, Any]]:
    """캐시(분봉 20초·일봉 5분)를 거쳐 봉을 만든다."""
    ttl = TTL_MINUTE if iv.base == "1m" else TTL_DAILY
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] <= ttl:
        return hit[1]
    raws = parse(await collect(fetch, iv.base_count), end_stamped=end_stamped and iv.base == "1m")
    out = aggregate(raws, iv, tz)
    _cache[key] = (now, out)
    if len(_cache) > 256:  # 오래된 것부터 버린다
        for k in sorted(_cache, key=lambda x: _cache[x][0])[:64]:
            _cache.pop(k, None)
    return out


def stock_fetch(client: Any, symbol: str, iv: Interval) -> Fetch:
    async def fetch(before: datetime | None) -> Page:
        rows, nxt = await client.candles(symbol, iv.base, PAGE, before)
        return list(rows), nxt

    return fetch


def index_fetch(client: Any, symbol: str, iv: Interval) -> Fetch:
    async def fetch(before: datetime | None) -> Page:
        return await client.index_candles_page(symbol, iv.base, PAGE, before)

    return fetch


# 주요 지표 → 차트 출처 (D-40 의 지표 띠와 같은 키)
INDEX_SOURCES: dict[str, dict[str, Any]] = {
    "KOSPI": {"kind": "index", "tz": KST, "minutes": True, "currency": None},
    "KOSDAQ": {"kind": "index", "tz": KST, "minutes": True, "currency": None},
    "KR_BOND_10Y": {"kind": "index", "tz": KST, "minutes": False, "currency": None},
    "QQQ": {"kind": "stock", "tz": ZoneInfo("America/New_York"), "minutes": True, "currency": "USD"},
    "SPY": {"kind": "stock", "tz": ZoneInfo("America/New_York"), "minutes": True, "currency": "USD"},
}
NO_CHART = {"USDKRW": "토스증권 Open API 에 환율 차트(봉)가 없어 현재 환율만 보여 줘요."}
