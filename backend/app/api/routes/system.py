"""시스템·대시보드 API: 상태, 포트폴리오, 자산 곡선."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.routes.common import C, U, _iso, _paper_only, _since
from app.container import Container, market_profile
from app.db.models import EquitySnapshot

router = APIRouter()


DISCLAIMER = "모의투자(PAPER) 모드입니다. 에이전트의 판단과 예시 전략은 투자 권유가 아닙니다."
US_DISCLAIMER = (
    "미장 모의투자(PAPER, 달러 가상계좌)입니다 — 실거래 설정과 관계없이 미국 주식은 주문이 나가지 않습니다. "
    "에이전트의 판단은 투자 권유가 아닙니다."
)
LIVE_DISCLAIMER = (
    "실거래(LIVE) 모드: 토스증권 실계좌로 실제 주문이 나갑니다. 에이전트의 판단은 투자 권유가 아니며, "
    "손실을 포함한 모든 결과는 사용자 책임입니다."
)


def _interval(c: Container) -> int:
    return c.scheduler.interval_minutes


@router.get("/system")
def system(c: C, _: U) -> dict[str, Any]:
    s = c.settings
    us = _paper_only(c)
    kv = c.llm_kv or c.kv
    return {
        "market": c.code,
        "currency": c.market.currency if c.market else "KRW",
        "timezone": str(c.cal.tz),
        "paper_only": us,
        "trading_mode": "paper" if us else s.trading_mode,
        "data_source": "toss",
        "toss_ok": c.engine.toss_ok,
        "toss_issue": c.kv.get("toss_health").get("reason") if not c.engine.toss_ok else None,
        "now": c.clock.now().isoformat(),
        "agent_interval_minutes": _interval(c),
        "llm": {
            "configured": c.runner.llm is not None,
            "model": c.runner.llm.model if c.runner.llm else None,
            "profile": market_profile(kv, s, c.code),
            "tool_mode": s.llm_tool_mode,
        },
        "research": c.research.sources,
        "engine_status": c.engine.status,
        "agent_running": c.runner.running,
        "order_window": c.engine.order_window(),
        "latest_price_day": (d.isoformat() if (d := c.store.latest_day()) else None),
        "next_runs": c.scheduler.next_runs(),
        "persona": c.runner.persona(),
        "disclaimer": US_DISCLAIMER if us else LIVE_DISCLAIMER if s.is_live else DISCLAIMER,
    }


@router.get("/portfolio")
async def portfolio(c: C, _: U) -> dict[str, Any]:
    return await c.engine.portfolio()


@router.get("/equity")
def equity(c: C, _: U, limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
    q = select(EquitySnapshot).order_by(EquitySnapshot.id.desc()).limit(limit)
    if since := _since(c):
        q = q.where(EquitySnapshot.ts >= since)
    with c.db.session() as s:
        rows = list(s.scalars(q))
    return [{"ts": _iso(r.ts), "equity": r.equity, "cash": r.cash} for r in reversed(rows)]
