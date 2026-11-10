"""일 단위 성과: 계좌 하루 마감 자산과 주요 지수 종가를 같은 날짜 축에 놓는다 (홈 '지수 대비 성과').

계좌는 자산 스냅샷(10분 간격)의 그 시장 현지 날짜별 마지막 값, 지수는 benchmark_daily(D-72) 종가.
둘 다 첫날을 0% 로 맞춘 누적 수익률(%)로 돌려준다. 지수 종가가 없는 날(휴장)은 직전 종가를 이어 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import BenchmarkDaily, EquitySnapshot
from app.db.session import Database
from app.market.indicators import INDICATORS

BENCHMARKS = {"KR": ("KOSPI", "KOSDAQ"), "US": ("SPY", "QQQ", "DIA")}
NAMES = {key: name for key, name, *_ in INDICATORS}


def _pct(v: float, base: float) -> float:
    return round((v / base - 1) * 100, 3) if base else 0.0


def daily_equity(db: Database, since: datetime, local_date: Callable[[datetime], date]) -> dict[date, float]:
    """그 시장 현지 날짜별 마지막 자산."""
    out: dict[date, float] = {}
    with db.session() as s:
        rows = s.execute(
            select(EquitySnapshot.ts, EquitySnapshot.equity)
            .where(EquitySnapshot.ts >= since)
            .order_by(EquitySnapshot.ts)
        )
        for ts, equity in rows:
            out[local_date(ts)] = equity  # 시간순이라 마지막 값이 남는다
    return out


def performance(
    db: Database,
    bench_db: Database,
    market: str,
    now: datetime,
    local_date: Callable[[datetime], date],
    days: int,
) -> dict[str, Any]:
    eq = daily_equity(db, now - timedelta(days=days), local_date)
    axis = sorted(eq)
    if not axis:
        return {"days": [], "account": [], "account_pct": [], "benchmarks": []}
    base = eq[axis[0]]
    keys = BENCHMARKS.get(market, ())
    closes: dict[str, dict[date, float]] = {k: {} for k in keys}
    with bench_db.session() as s:
        rows = s.execute(
            select(BenchmarkDaily.key, BenchmarkDaily.day, BenchmarkDaily.close)
            .where(BenchmarkDaily.key.in_(keys), BenchmarkDaily.day >= axis[0] - timedelta(days=10))
            .order_by(BenchmarkDaily.day)
        )
        for key, day, close in rows:
            closes[key][day] = close
    benchmarks = []
    for key in keys:
        series: list[float | None] = []
        last: float | None = None
        hist = sorted(closes[key].items())
        i = 0
        for d in axis:
            while i < len(hist) and hist[i][0] <= d:
                last = hist[i][1]
                i += 1
            series.append(last)
        first = next((v for v in series if v is not None), None)
        if first is None:
            continue
        benchmarks.append(
            {
                "key": key,
                "name": NAMES.get(key, key),
                "pct": [_pct(v, first) if v is not None else 0.0 for v in series],
            }
        )
    return {
        "days": [d.isoformat() for d in axis],
        "account": [eq[d] for d in axis],
        "account_pct": [_pct(eq[d], base) for d in axis],
        "benchmarks": benchmarks,
    }
