"""주요 지표 하루 종가 기록 (D-72) — 일 단위 분석('코스피·다우가 빠진 날 우리 자산은?')의 비교 기준.

- 지수·국고채(KOSPI·KOSDAQ·KR_BOND_10Y): 토스 지표 일봉 (GET /api/v1/market-indicators/{symbol}/candles, interval=1d)
- 미국 지수 대신 ETF(QQQ·SPY·DIA): 토스 종목 일봉 (GET /api/v1/candles, interval=1d) — 날짜는 뉴욕 기준
- 원/달러: 봉 API 가 없어 매일 16:10(한국 시각, 국장 마감 뒤) 현재 환율을 그날 값으로 (source=quote)

하루 두 번(07:10 미장 마감 뒤 · 16:10 국장 마감 뒤) 최근 일봉으로 덮어써 진행 중이던 봉도 마감값으로 맞춘다.
처음(표가 비었을 때)은 일봉 200개로 지난 기록을 채운다. 분 단위는 남기지 않는다(분석은 일 단위).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.core.clock import KST
from app.db.models import BenchmarkDaily
from app.db.session import Database
from app.market.indicators import INDICATORS

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
BACKFILL_DAYS = 200  # 토스 일봉 한 번 최대
REFRESH_DAYS = 10  # 평소: 최근 봉만 다시 (늦게 고쳐진 값 반영)
FX_HOUR = 16  # 환율은 이 슬롯(한국 시각)에만 남긴다


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def daily_closes(candles: list[dict[str, Any]], tz: ZoneInfo) -> dict[date, float]:
    """토스 일봉 → {시장 날짜: 종가}. 일봉 timestamp 는 그 봉의 시작 시각이다."""
    out: dict[date, float] = {}
    for c in candles:
        ts, close = c.get("timestamp"), _f(c.get("closePrice"))
        if not ts or close is None:
            continue
        try:
            day = datetime.fromisoformat(str(ts)).astimezone(tz).date()
        except ValueError:
            continue
        out[day] = close
    return out


def upsert(db: Database, key: str, rows: dict[date, float], source: str) -> int:
    """(key, day) 마다 하나 — 있으면 종가를 고친다. 고치거나 넣은 행 수."""
    if not rows:
        return 0
    n = 0
    with db.session() as s:
        have = {
            r.day: r
            for r in s.scalars(
                select(BenchmarkDaily).where(BenchmarkDaily.key == key, BenchmarkDaily.day.in_(list(rows)))
            )
        }
        for day, close in rows.items():
            r = have.get(day)
            if r is None:
                s.add(BenchmarkDaily(key=key, day=day, close=close, source=source))
                n += 1
            elif r.close != close or r.source != source:
                r.close, r.source = close, source
                n += 1
    return n


async def record(db: Database, client: Any, at: datetime) -> dict[str, int]:
    """지표마다 최근 일봉 종가를 남긴다(처음이면 200일). 한 지표가 실패해도 나머지는 남긴다. 지표별 고친 행 수."""
    with db.session() as s:
        empty = not s.scalar(select(func.count()).select_from(BenchmarkDaily))
    days = BACKFILL_DAYS if empty else REFRESH_DAYS
    out: dict[str, int] = {}
    for key, _name, kind, _unit, _note in INDICATORS:
        try:
            if kind == "fx":
                if at.astimezone(KST).hour != FX_HOUR:
                    continue
                rate = _f((await client.exchange_rate()).get("rate"))
                if rate is not None:
                    out[key] = upsert(db, key, {at.astimezone(KST).date(): rate}, "quote")
                continue
            if kind == "etf":
                rows, _ = await client.candles(key, "1d", days)
                closes = daily_closes(list(rows), NY)
            else:
                closes = daily_closes(await client.index_candles(key, days), KST)
            out[key] = upsert(db, key, closes, "candle")
        except Exception as e:  # 다음 슬롯에 다시
            log.warning("지표 종가 기록 실패 (%s): %s", key, type(e).__name__)
    log.info("주요 지표 하루 종가 기록: %s", out)
    return out


def history(db: Database, since: date) -> dict[str, list[dict[str, Any]]]:
    """지표별 [{day, close, source}] (오래된 순)."""
    out: dict[str, list[dict[str, Any]]] = {}
    with db.session() as s:
        rows = s.scalars(
            select(BenchmarkDaily)
            .where(BenchmarkDaily.day >= since)
            .order_by(BenchmarkDaily.key, BenchmarkDaily.day)
        )
        for r in rows:
            out.setdefault(r.key, []).append({"day": r.day.isoformat(), "close": r.close, "source": r.source})
    return out
