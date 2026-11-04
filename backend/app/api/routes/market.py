"""시장·종목 API: 주요 지표, 종목 검색, 차트, 리서치."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.routes.common import C, U
from app.container import Container
from app.db.models import AgentDecision, Fill

router = APIRouter()


# ====================================================================== 시장/종목
@router.get("/market/indicators")
async def market_indicators(c: C, _: U) -> dict[str, Any]:
    """대시보드 상단 주요 지표 (1분 캐시, D-40)."""
    return await c.indicators.snapshot()


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
