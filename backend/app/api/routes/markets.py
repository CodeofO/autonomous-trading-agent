"""국장·미장 API (D-48): 시장 목록, 합산 요약."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.routes.common import C, U, _containers, _iso, _since
from app.container import Container, market_profile
from app.core.clock import KST
from app.db.models import AgentRun

log = logging.getLogger(__name__)
router = APIRouter()


# ====================================================================== 국장·미장 (D-48)
_fx_cache: dict[str, Any] = {}
FX_TTL = 60.0


async def _usdkrw(c: Container) -> dict[str, Any]:
    """원/달러 (토스 `/exchange-rate`, 1분 캐시). 실패하면 rate=None."""
    now = time.monotonic()
    if _fx_cache.get("at_mono") and now - _fx_cache["at_mono"] < FX_TTL:
        return {"usdkrw": _fx_cache["rate"], "at": _fx_cache["at"]}
    try:
        fx = await c.toss.client.exchange_rate()
        rate = float(fx.get("rate") or 0) or None
    except Exception as e:
        log.warning("원/달러 환율 조회 실패: %s", type(e).__name__)
        return {"usdkrw": None, "at": None}
    at = str(fx.get("validFrom") or c.clock.now().isoformat())
    _fx_cache.update(rate=rate, at=at, at_mono=now)
    return {"usdkrw": rate, "at": at}


def _session_dict(s: Any) -> dict[str, str]:
    return {
        "day": s.day.isoformat(),
        "open": s.open.astimezone(KST).isoformat(),
        "close": s.close.astimezone(KST).isoformat(),
        "order_cutoff": s.order_cutoff.astimezone(KST).isoformat(),
    }


def _session_info(c: Container) -> dict[str, Any]:
    """오늘(현지 거래일) 정규장과 다음 정규장 — 모두 한국 시각 ISO.

    next_session: 지금 장중이면 다음 거래일 장, 아니면 아직 열리지 않은 가장 가까운 장. 미장은 한국 아침에
    '현지 오늘'이 이미 끝난 전날 장이라, 화면 시간표(06:00→다음날 06:00)는 이 값으로 오늘 밤 장을 그린다.
    """
    now = c.clock.now()
    s = c.cal.session(c.cal.local_date(now))
    nxt = c.cal.next_session(now)
    if nxt.open <= now:  # 지금 장중 → 다음 거래일 장
        nxt = c.cal.next_session(nxt.close + timedelta(minutes=1))
    return {
        "open_now": c.cal.is_market_open(now),
        "session": _session_dict(s) if s is not None else None,
        "next_session": _session_dict(nxt),
        "next_session_open": nxt.open.astimezone(KST).isoformat(),
    }


@router.get("/markets")
def markets(request: Request, c: C, _: U) -> list[dict[str, Any]]:
    """시장 목록(국장·미장): 통화·시간대·장 상태·LLM 프로필. 미장이 꺼져 있으면 enabled=false."""
    from app.core.market import MARKETS, spec_for

    cs = _containers(request)
    out = []
    for code in MARKETS:
        st = cs.get(code)
        spec = st.market if st is not None and st.market else spec_for(code, c.settings)
        row: dict[str, Any] = {
            "code": code,
            "name": spec.name,
            "currency": spec.currency,
            "timezone": str(spec.tz),
            "enabled": st is not None,
            "paper_only": not spec.live,
            "trading_mode": "live" if spec.live else "paper",
            "llm_profile": market_profile(c.kv, c.settings, code),
            "llm_model": st.runner.llm.model if st is not None and st.runner.llm else None,
            "agent_running": bool(st is not None and st.runner.running),
            "engine_status": st.engine.status if st is not None else None,
        }
        if st is not None:
            row.update(_session_info(st))
        else:
            row.update(open_now=False, session=None, next_session=None, next_session_open=None)
        out.append(row)
    return out


async def _market_overview(st: Container) -> dict[str, Any]:
    pf = await st.engine.portfolio()
    sod = st.engine.start_of_day_equity(float(pf["equity"]))
    invested = sum(float(p["market_value"]) for p in pf["positions"])
    since = _since(st)
    with st.db.session() as s:
        q = select(AgentRun).order_by(AgentRun.id.desc()).limit(1)
        if since:
            q = q.where(AgentRun.started_at >= since)
        last = s.scalar(q)
    nxt = st.scheduler.next_runs(1)
    return {
        "code": st.code,
        "name": st.market.name if st.market else "국장",
        "currency": st.market.currency if st.market else "KRW",
        "equity": pf["equity"],
        "cash": pf["cash"],
        "invested": st.engine.money(invested),
        "pnl_today": st.engine.money(float(pf["equity"]) - sod),
        "pnl_today_pct": pf["daily_pnl_pct"],
        "total_return_pct": pf["total_return_pct"],
        "positions": len(pf["positions"]),
        "top_positions": [
            {"symbol": p["symbol"], "name": p["name"], "weight": p["weight_pct"], "pnl_pct": p["pnl_pct"]}
            for p in pf["positions"][:5]
        ],
        "engine_status": st.engine.status,
        "agent_running": st.runner.running,
        "next_run": nxt[0] if nxt else None,
        "last_session": {
            "run_id": last.id,
            "at": _iso(last.started_at),
            "status": last.status,
            "summary": last.summary[:300],
        }
        if last is not None
        else None,
    }


@router.get("/overview")
async def overview(request: Request, c: C, _: U) -> dict[str, Any]:
    """국장·미장 합산 요약. 미장 금액은 달러 그대로 두고, 합계(total_krw)만 토스 원/달러로 환산한다."""
    # 환율과 시장별 요약을 동시에 (B9) — 순서는 시장 순서 그대로
    fx, *rows = await asyncio.gather(
        _usdkrw(c), *(_market_overview(st) for st in _containers(request).values())
    )
    rate = fx["usdkrw"]
    complete = rate is not None or all(r["currency"] == "KRW" for r in rows)

    def krw(r: dict[str, Any], key: str) -> float:
        return float(r[key]) * (rate or 0) if r["currency"] == "USD" else float(r[key])

    return {
        "fx": fx,
        "markets": rows,
        "total_krw": {
            "equity": round(sum(krw(r, "equity") for r in rows)) if complete else None,
            "pnl_today": round(sum(krw(r, "pnl_today") for r in rows)) if complete else None,
        },
    }
