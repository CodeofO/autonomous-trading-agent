"""장 마감 후 일일 리포트."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from app.db.models import AgentDecision, AgentRun, AuditEvent, DailyReport, Fill, Order
from app.engine.engine import TradingEngine
from app.engine.events import SYSTEM


async def build_daily_report(engine: TradingEngine) -> dict[str, Any]:
    # 거래일 = 그 시장의 현지 날짜 (미장은 뉴욕 날짜 — 한국 시각 새벽 마감 리포트도 전날 거래일로 묶인다)
    tz = engine.cal.tz
    day = engine.today()
    start = datetime.combine(day, time(0), tz)
    end = start + timedelta(days=1)
    pf = await engine.portfolio()
    with engine.db.session() as s:
        fills = list(
            s.scalars(select(Fill).where(Fill.filled_at >= start - timedelta(days=4), Fill.filled_at < end))
        )
        orders_today = list(s.scalars(select(Order).where(Order.created_at >= start, Order.created_at < end)))
        blocks = (
            s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.type == "RISK_BLOCK", AuditEvent.ts >= start, AuditEvent.ts < end)
            )
            or 0
        )
        runs = list(
            s.scalars(select(AgentRun).where(AgentRun.started_at >= start, AgentRun.started_at < end))
        )
        decisions = list(
            s.scalars(select(AgentDecision).where(AgentDecision.run_id.in_([r.id for r in runs])))
        )
    settled_today = [
        f for f in fills if _aware(f.filled_at).astimezone(tz).date() >= engine.cal.previous_trading_day(day)
    ]
    sod = engine.start_of_day_equity(pf["equity"])
    report = {
        "day": day.isoformat(),
        "equity": pf["equity"],
        "cash": pf["cash"],
        "start_of_day_equity": engine.money(sod),
        "daily_pnl": engine.money(pf["equity"] - sod),
        "daily_pnl_pct": pf["daily_pnl_pct"],
        "total_return_pct": pf["total_return_pct"],
        "realized_pnl": pf["realized_pnl"],
        "positions": pf["positions"],
        "orders_placed": len(orders_today),
        "fills": [
            {
                "symbol": f.symbol,
                "side": f.side,
                "quantity": f.quantity,
                "price": f.price,
                "filled_at": _aware(f.filled_at).isoformat(),
            }
            for f in settled_today
        ],
        "risk_blocks": blocks,
        "agent_runs": [
            {"id": r.id, "trigger": r.trigger, "status": r.status, "summary": r.summary[:300]} for r in runs
        ],
        "decisions": {a: sum(d.action == a for d in decisions) for a in ("BUY", "SELL", "HOLD")},
    }
    with engine.db.session() as s:
        s.merge(DailyReport(day=day, payload=report, created_at=datetime.now(UTC)))
    if engine.market is not None:
        report["market"], report["currency"] = engine.market.code, engine.market.currency
    sign = "📈" if report["daily_pnl"] >= 0 else "📉"
    if engine.currency == "USD":
        eq, pnl = (
            f"${report['equity']:,.2f}",
            ("+" if report["daily_pnl"] >= 0 else "-") + (f"${abs(report['daily_pnl']):,.2f}"),
        )
        title = f"📊 미장 {day:%m/%d}(미국) 마감 리포트"
    else:
        eq, pnl = f"{report['equity']:,.0f}원", f"{report['daily_pnl']:+,.0f}원"
        title = f"📊 {day:%m/%d} 마감 리포트"
    engine.hub.chat(
        SYSTEM,
        "summary",
        f"{title} {sign} 총자산 {eq} "
        f"(오늘 {pnl}, 누적 {report['total_return_pct']:+.2f}%) · "
        f"주문 {report['orders_placed']}건 · 리스크 차단 {blocks}건",
        report=report,
    )
    engine.hub.audit("REPORT", f"{day} 일일 리포트 생성", None)
    return report


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)
