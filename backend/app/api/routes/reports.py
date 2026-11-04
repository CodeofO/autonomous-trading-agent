"""백테스트·일일 리포트 API."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.routes.common import C, U
from app.backtest.engine import run_backtest
from app.db.models import DailyReport
from app.strategies.rules import REGISTRY, build

router = APIRouter()


# ====================================================================== 백테스트 / 리포트
class BacktestBody(BaseModel):
    strategy_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(min_length=1, max_length=20)
    start: date
    end: date
    initial_cash: float = Field(10_000_000, gt=0)
    position_size: float = Field(0.1, gt=0, le=1)


@router.post("/backtest")
async def backtest(body: BacktestBody, c: C, _: U) -> dict[str, Any]:
    if body.strategy_type not in REGISTRY:
        raise HTTPException(422, "알 수 없는 전략")
    if body.end <= body.start or (body.end - body.start).days > 3 * 366:
        raise HTTPException(422, "기간은 시작<종료, 최대 3년입니다")
    try:
        build(body.strategy_type, body.params)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    latest = c.cal.latest_published_day(c.clock.now())
    end = min(body.end, latest)
    bars = []
    for sym in body.symbols:
        await c.store.ensure_history(sym, end, (end - body.start).days + 120)
        bars += [b for b in c.store.history(sym, end, 2000) if b.day >= body.start - timedelta(days=200)]
    if not bars:
        raise HTTPException(422, "해당 종목·기간의 시세가 없습니다")
    res = await run_backtest(
        bars,
        body.strategy_type,
        body.params,
        body.symbols,
        body.start,
        end,
        initial_cash=body.initial_cash,
        position_size=body.position_size,
        risk=c.engine.risk.config,
        costs=c.broker.costs,
        calendar=c.cal,
    )
    c.hub.audit(
        "BACKTEST", f"백테스트 {body.strategy_type} {body.start}~{end}: {res.total_return_pct:+.2f}%", None
    )
    return res.__dict__


@router.get("/reports")
def reports(c: C, _: U, limit: int = Query(30, ge=1, le=365)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [
            r.payload for r in s.scalars(select(DailyReport).order_by(DailyReport.day.desc()).limit(limit))
        ]


@router.post("/reports/now")
async def report_now(c: C, _: U) -> dict[str, Any]:
    from app.reports.daily import build_daily_report

    return await build_daily_report(c.engine)
