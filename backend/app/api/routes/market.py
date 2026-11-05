"""시장·종목 API: 주요 지표, 종목 검색, 차트, 리서치."""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.routes.common import C, U
from app.container import Container
from app.core.clock import KST
from app.db.models import AgentDecision, Fill
from app.market import benchmarks as bm
from app.market import chart as ch

router = APIRouter()


# ====================================================================== 시장/종목
@router.get("/market/indicators")
async def market_indicators(c: C, _: U) -> dict[str, Any]:
    """대시보드 상단 주요 지표 (1분 캐시, D-40)."""
    return await c.indicators.snapshot()


@router.get("/market/benchmarks")
def market_benchmarks(c: C, _: U, days: int = Query(90, ge=1, le=3660)) -> dict[str, Any]:
    """주요 지표 하루 종가 기록 (D-72) — 일 단위 분석의 비교 기준. 국장 DB(공용)에 쌓인다."""
    today = c.clock.now().astimezone(KST).date()
    return {
        "since": (today - timedelta(days=days)).isoformat(),
        "items": bm.history(c.db, today - timedelta(days=days)),
    }


@router.get("/market/indicators/{key}/chart")
async def indicator_chart(key: str, c: C, _: U, interval: str = Query("1d", max_length=4)) -> dict[str, Any]:
    """주요 지표 차트 (D-63): 코스피·코스닥(분·일), 국고채(일만), 나스닥100·S&P500(QQQ·SPY ETF, 분·일).
    환율은 Open API 에 봉이 없어 404."""
    if key in ch.NO_CHART:
        raise HTTPException(404, ch.NO_CHART[key])
    src = ch.INDEX_SOURCES.get(key)
    if src is None:
        raise HTTPException(404, "차트를 볼 수 없는 지표예요")
    try:
        iv = ch.get(interval)
    except ch.ChartError as e:
        raise HTTPException(400, str(e)) from e
    if iv.base == "1m" and not src["minutes"]:
        raise HTTPException(400, "국고채 금리는 일·주·월 봉만 있어요")
    client = c.toss.client
    fetch = (ch.index_fetch if src["kind"] == "index" else ch.stock_fetch)(client, key, iv)
    try:
        bars = await ch.bars(("ind", key, iv.key), iv, src["tz"], fetch, end_stamped=src["kind"] == "stock")
    except Exception as e:
        raise HTTPException(503, f"토스증권 차트를 불러오지 못했어요: {type(e).__name__}") from e
    return {
        "key": key,
        "interval": iv.key,
        "intraday": iv.base == "1m",
        "currency": src["currency"],
        "intervals": ch.catalog(minutes=bool(src["minutes"])),
        "bars": bars,
        "markers": [],
    }


@router.get("/market/search")
async def market_search(
    c: C, _: U, q: str = Query("", max_length=60), limit: int = Query(20, ge=1)
) -> list[dict[str, Any]]:
    """종목 검색 (메모 카드 종목 고르기): 국장 = 코드 앞자리·이름 일부, 미장 = 티커 앞자리·한글/영문 이름 일부. 최대 50개."""
    if not q.strip():
        return []
    try:
        return await c.toss.find(q, min(limit, 50))
    except Exception as e:
        raise HTTPException(503, f"토스증권 종목 목록을 불러오지 못했습니다: {type(e).__name__}") from e


@router.get("/market/universe")
async def universe(c: C, _: U, q: str = "", limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        return await c.toss.search(q, limit)
    except Exception as e:
        raise HTTPException(503, f"토스증권 종목 목록을 불러오지 못했습니다: {type(e).__name__}") from e


@router.get("/market/{symbol}/candles")
async def candles(symbol: str, c: C, _: U, days: int = Query(180, ge=20, le=1000)) -> dict[str, Any]:
    latest = c.cal.local_date(c.clock.now())  # 국장 KST 날짜 / 미장 뉴욕 날짜
    if latest is not None:
        await c.store.ensure_history(symbol, latest, int(days * 1.5))
    bars = c.store.history(symbol, latest, days)
    with c.db.session() as s:
        fl = list(s.scalars(select(Fill).where(Fill.symbol == symbol)))
        decs = list(
            s.scalars(
                select(AgentDecision).where(AgentDecision.symbol == symbol, AgentDecision.action != "HOLD")
            )
        )
    markers = [
        {
            "time": f.filled_at.astimezone(c.cal.tz).date().isoformat() if f.filled_at else None,
            "side": f.side,
            "kind": "fill",
            "text": f"{'매수' if f.side == 'BUY' else '매도'} {f.quantity}@{_px(c, f.price)}",
        }
        for f in fl
    ]
    markers += [
        {
            "time": d.created_at.astimezone(c.cal.tz).date().isoformat() if d.created_at else None,
            "side": d.action,
            "kind": "decision",
            "text": f"AI {d.action} ({d.confidence:.0%})",
        }
        for d in decs
    ]
    return {
        "symbol": symbol,
        "name": bars[-1].name if bars else c.store.name_of(symbol),
        "currency": c.market.currency if c.market else "KRW",
        "bars": [
            {
                "time": b.day.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ],
        "markers": [m for m in markers if m["time"]],
    }


@router.get("/market/{symbol}/chart")
async def stock_chart(symbol: str, c: C, _: U, interval: str = Query("1d", max_length=4)) -> dict[str, Any]:
    """종목 차트 (D-63): 1·5·15·30·60분, 일·주·월 봉 + 체결·AI 결정 표시."""
    try:
        iv = ch.get(interval)
    except ch.ChartError as e:
        raise HTTPException(400, str(e)) from e
    tz = c.cal.tz
    fetch = ch.stock_fetch(c.toss.client, symbol, iv)
    try:
        bars = await ch.bars((c.code, symbol, iv.key), iv, tz, fetch, end_stamped=True)
    except Exception as e:
        raise HTTPException(503, f"토스증권 차트를 불러오지 못했어요: {type(e).__name__}") from e
    keys = {b["time"] for b in bars}
    with c.db.session() as s:
        fl = list(s.scalars(select(Fill).where(Fill.symbol == symbol)))
        decs = list(
            s.scalars(
                select(AgentDecision).where(AgentDecision.symbol == symbol, AgentDecision.action != "HOLD")
            )
        )
    marks = [
        (
            f.filled_at,
            f.side,
            "fill",
            f"{'매수' if f.side == 'BUY' else '매도'} {f.quantity}@{_px(c, f.price)}",
        )
        for f in fl
    ] + [(d.created_at, d.action, "decision", f"AI {d.action} ({d.confidence:.0%})") for d in decs]
    markers = []
    for at, side, kind, text in marks:
        if at is None:
            continue
        k = ch.bucket(at if at.tzinfo else at.replace(tzinfo=UTC), iv, tz)
        if k in keys:
            markers.append({"time": k, "side": side, "kind": kind, "text": text})
    return {
        "symbol": symbol,
        "name": c.store.name_of(symbol) or symbol,
        "currency": c.market.currency if c.market else "KRW",
        "interval": iv.key,
        "intraday": iv.base == "1m",
        "intervals": ch.catalog(),
        "bars": bars,
        "markers": markers,
    }


def _px(c: Container, px: float) -> str:
    return f"${px:,.2f}" if c.market and c.market.is_usd else f"{px:,.0f}"


@router.get("/market/{symbol}/research")
async def research(symbol: str, c: C, _: U) -> dict[str, Any]:
    tb = c.runner.tb
    name = c.store.name_of(symbol) or symbol
    if c.code == "US":  # 미장: 재무(DART)·공시·국내 수급 없음
        news, status = await asyncio.gather(
            tb.call("search_news", {"query": name, "limit": 8}),
            tb.call("get_stock_status", {"symbol": symbol}),
        )
        na = {"available": False, "reason": "미국 주식은 지원하지 않아요 (국내 전용)"}
        return {
            "symbol": symbol,
            "name": name,
            "currency": "USD",
            "financials": na,
            "news": news,
            "disclosures": na,
            "supply": na,
            "status": status,
        }
    fin, news, disc, supply, status = await asyncio.gather(
        tb.call("get_financials", {"symbol": symbol}),
        tb.call("search_news", {"query": name, "limit": 8}),
        tb.call("get_disclosures", {"symbol": symbol, "days": 60}),
        tb.call("get_supply_demand", {"symbol": symbol, "days": 10}),
        tb.call("get_stock_status", {"symbol": symbol}),
    )
    return {
        "symbol": symbol,
        "name": name,
        "financials": fin,
        "news": news,
        "disclosures": disc,
        "supply": supply,
        "status": status,
    }
