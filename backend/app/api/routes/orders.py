"""주문·체결 API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.routes.common import C, U, _iso, _since
from app.db.models import Fill, Order
from app.engine.orders import order_dict

router = APIRouter()


# ====================================================================== 주문
@router.get("/orders")
def orders(
    c: C, _: U, status: Literal["open", "closed", "all"] = "all", limit: int = Query(200, ge=1, le=2000)
) -> list[dict[str, Any]]:
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    open_states = ["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]
    if status == "open":
        q = q.where(Order.status.in_(open_states))
    elif status == "closed":
        q = q.where(Order.status.not_in(open_states))
    if since := _since(c):
        q = q.where(Order.created_at >= since)
    with c.db.session() as s:
        rows = [order_dict(o) for o in s.scalars(q)]
    names = c.store.names({o["symbol"] for o in rows})  # 종목 이름은 한 번에 (B1)
    return [o | {"name": names.get(o["symbol"], "")} for o in rows]


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, c: C, _: U) -> dict[str, Any]:
    o = await c.engine.orders.cancel(order_id)
    if o is None:
        raise HTTPException(409, "취소할 수 없는 주문입니다(이미 종료됐거나 없음)")
    return order_dict(o)


@router.get("/fills")
def fills(c: C, _: U, limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
    q = select(Fill).order_by(Fill.id.desc()).limit(limit)
    if since := _since(c):
        q = q.where(Fill.filled_at >= since)
    with c.db.session() as s:
        rows = list(s.scalars(q))
    names = c.store.names({f.symbol for f in rows})  # 종목 이름은 한 번에 (B1)
    return [
        {
            "id": f.id,
            "order_id": f.order_id,
            "symbol": f.symbol,
            "name": names.get(f.symbol, ""),
            "side": f.side,
            "quantity": f.quantity,
            "price": f.price,
            "fee": f.fee,
            "tax": f.tax,
            "filled_at": _iso(f.filled_at),
        }
        for f in rows
    ]
