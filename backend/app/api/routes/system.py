"""시스템·대시보드 API: 상태, 포트폴리오, 자산 곡선."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.routes.common import C, U, _is_live, _is_us, _iso, _since
from app.container import Container, market_profile
from app.db.models import EquitySnapshot
from app.engine.performance import performance as daily_performance

router = APIRouter()


DISCLAIMER = "모의투자(PAPER) 모드입니다. 에이전트의 판단과 예시 전략은 투자 권유가 아닙니다."
US_DISCLAIMER = (
    "미장 모의투자(PAPER, 달러 가상계좌)입니다 — 미국 주식 실제 주문은 나가지 않습니다. "
    "에이전트의 판단은 투자 권유가 아닙니다."
)
US_LIVE_DISCLAIMER = (
    "미장 실거래(LIVE) 모드: 토스증권 실계좌로 미국 주식 실제 주문이 나갑니다(달러). 에이전트의 판단은 "
    "투자 권유가 아니며, 손실을 포함한 모든 결과는 사용자 책임입니다."
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
    us, live = _is_us(c), _is_live(c)
    kv = c.llm_kv or c.kv
    disclaimer = (
        (US_LIVE_DISCLAIMER if live else US_DISCLAIMER) if us else LIVE_DISCLAIMER if live else DISCLAIMER
    )
    return {
        "market": c.code,
        "currency": c.market.currency if c.market else "KRW",
        "timezone": str(c.cal.tz),
        "paper_only": not live,  # 지금 이 시장이 모의계좌인지 (실거래는 .env 에서 시장마다 따로 켠다)
        "trading_mode": "live" if live else "paper",
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
        "disclaimer": disclaimer,
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


@router.get("/performance")
def performance(c: C, _: U, days: int = Query(90, ge=2, le=3660)) -> dict[str, Any]:
    """일 단위 누적 수익률: 이 계좌 vs 주요 지수 (국장 코스피·코스닥, 미장 S&P500·나스닥100·다우 ETF)."""
    bench_db = c.runner.usage_db or c.db  # 지수 종가는 국장 DB(공용)에 쌓인다 (D-72)
    return daily_performance(c.db, bench_db, c.code, c.clock.now(), c.cal.local_date, days)
