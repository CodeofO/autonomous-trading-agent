"""매매 내역 원장: 체결 기록을 순서대로 다시 계산해 매도별 실현손익과 매매 사유를 만든다.

모의계좌(PaperBroker._apply_fill)와 같은 평균단가 방식이다.
- 매수: 평단 = (기존 평단 × 수량 + 체결금액 + 수수료) / 새 수량
- 매도: 실현손익 = (체결가 − 평단) × 수량 − 수수료 − 세금
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.clock import KST
from app.db.models import Fill, Order
from app.db.session import Database


def trade_ledger(
    db: Database,
    names: Callable[[str], str] | None = None,
    since: datetime | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """최근 체결 내역(최신순, 최대 limit 건)과 종목별·전체 실현손익."""
    with db.session() as s:
        q = select(Fill, Order).join(Order, Fill.order_id == Order.id).order_by(Fill.filled_at, Fill.id)
        if since is not None:
            q = q.where(Fill.filled_at >= since)
        rows = list(s.execute(q).tuples())

    pos: dict[str, tuple[int, float]] = {}  # symbol → (수량, 평단)
    trades: list[dict[str, Any]] = []
    by_symbol: dict[str, float] = {}
    total = fees = taxes = 0.0
    for f, o in rows:
        fees += f.fee
        taxes += f.tax
        qty, avg = pos.get(f.symbol, (0, 0.0))
        name = (names(f.symbol) if names else "") or f.symbol
        t: dict[str, Any] = {
            "time": f.filled_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "symbol": f.symbol,
            "name": name,
            "side": f.side,
            "quantity": f.quantity,
            "price": round(f.price),
            "reason": o.reason or "",
            "by": "에이전트 판단" if o.run_id else "자동(손절·익절 등)",
        }
        if o.run_id:
            t["session"] = o.run_id
        if f.side == "BUY":
            new_qty = qty + f.quantity
            pos[f.symbol] = (new_qty, (avg * qty + f.price * f.quantity + f.fee) / new_qty)
        elif qty < f.quantity:
            # 이 시스템 밖에서 산 주식(실계좌 기존 보유분 등)이 섞인 매도 → 평단을 몰라 계산하지 않는다
            t["realized_pnl"] = None
            t["note"] = "매수 기록이 없어 실현손익을 계산할 수 없음 (시스템 밖 보유분)"
            pos.pop(f.symbol, None)
        else:
            pnl = (f.price - avg) * f.quantity - f.fee - f.tax
            t["avg_cost"] = round(avg)
            t["realized_pnl"] = round(pnl)
            total += pnl
            by_symbol[name] = by_symbol.get(name, 0.0) + pnl
            left = qty - f.quantity
            if left > 0:
                pos[f.symbol] = (left, avg)
            else:
                pos.pop(f.symbol, None)
        trades.append(t)

    return {
        "realized_pnl_total": round(total),
        "realized_pnl_by_stock": {k: round(v) for k, v in by_symbol.items()},
        "fees_total": round(fees),
        "taxes_total": round(taxes),
        "fill_count": len(trades),
        "recent_fills": list(reversed(trades))[:limit],
    }
