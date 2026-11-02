"""주문 관리자: 리스크 검사 → 주문 생성(멱등키) → 상태 머신 → 체결 기록 → 감사 로그/채팅."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.brokers.base import (
    TERMINAL,
    BrokerInterface,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.brokers.paper import PaperBroker
from app.db.models import Fill, Order
from app.db.session import Database
from app.engine.events import BROKER, RISK, EventHub
from app.engine.state_machine import walk
from app.risk.rules import OrderIntent, RiskContext, RiskDecision, RiskManager

ContextProvider = Callable[[], Awaitable[RiskContext]]

_REJECT_KO = {
    "order-hours-closed": "장 운영시간이 아니에요",
    "insufficient-buying-power": "주문 가능 현금이 부족해요",
    "insufficient-sellable-quantity": "매도 가능 수량이 부족해요",
    "limit-not-reached": "지정가에 닿지 않아 미체결 취소됐어요",
    "no-volume": "거래량이 없어 체결되지 않았어요",
    "partial-fill-volume-cap": "거래량 한도로 일부만 체결하고 나머지는 취소했어요",
    "insufficient-cash-at-fill": "체결 시점 현금이 부족해 취소됐어요",
    "canceled-by-user": "취소 요청으로 취소됐어요",
    "kill-switch": "킬 스위치로 취소됐어요",
}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def order_dict(o: Order) -> dict[str, Any]:
    return {
        "id": o.id,
        "client_order_id": o.client_order_id,
        "broker_order_id": o.broker_order_id,
        "symbol": o.symbol,
        "side": o.side,
        "order_type": o.order_type,
        "quantity": o.quantity,
        "limit_price": o.limit_price,
        "status": o.status,
        "filled_qty": o.filled_qty,
        "avg_fill_price": o.avg_fill_price,
        "reason": o.reason,
        "reject_reason": o.reject_reason,
        "fill_basis": o.fill_basis,
        "trade_day": o.trade_day.isoformat() if o.trade_day else None,
        "run_id": o.run_id,
        "strategy_id": o.strategy_id,
        "created_at": _aware(o.created_at).isoformat(),
        "updated_at": _aware(o.updated_at).isoformat(),
    }


class OrderManager:
    def __init__(
        self,
        db: Database,
        broker: BrokerInterface,
        risk: RiskManager,
        hub: EventHub,
        context: ContextProvider,
        name_of: Callable[[str], str] | None = None,
    ) -> None:
        self.db = db
        self.name_of = name_of or (lambda sym: sym)
        self.broker = broker
        self.risk = risk
        self.hub = hub
        self.context = context

    def _label(self, symbol: str) -> str:
        name = self.name_of(symbol)
        return f"{name}({symbol})" if name and name != symbol else symbol

    # ------------------------------------------------------------ 조회 헬퍼
    def open_symbols(self) -> set[str]:
        with self.db.session() as s:
            rows = s.scalars(
                select(Order.symbol).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
            )
            return set(rows)

    def last_order_times(self) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        with self.db.session() as s:
            for sym, ts in s.execute(
                select(Order.symbol, Order.created_at).where(Order.status != "REJECTED")
            ):
                ts = _aware(ts)
                if sym not in out or ts > out[sym]:
                    out[sym] = ts
        return out

    # ------------------------------------------------------------ 제출
    async def submit(
        self,
        intent: OrderIntent,
        *,
        run_id: int | None = None,
        strategy_id: int | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        source: str = "agent",
    ) -> tuple[Order | None, RiskDecision]:
        ctx = await self.context()
        decision = self.risk.check(intent, ctx)
        name = self._label(intent.symbol)
        if not decision.allowed:
            self.hub.audit(
                "RISK_BLOCK",
                decision.reasons[0],
                intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                rule=decision.rule,
                source=source,
                run_id=run_id,
            )
            self.hub.chat(
                RISK,
                "block",
                f"🛑 잠깐! {name} {_side_ko(intent.side)} {intent.quantity}주는 막을게요. "
                f"{decision.reasons[0]}",
                run_id=run_id,
                symbol=intent.symbol,
                rule=decision.rule,
            )
            return None, decision
        if decision.notes:
            self.hub.chat(
                RISK,
                "text",
                f"⚖️ {name} {_side_ko(intent.side)} 수량 조정: " + " / ".join(decision.notes),
                run_id=run_id,
                symbol=intent.symbol,
            )
        req = OrderRequest(
            client_order_id=uuid.uuid4().hex,
            symbol=intent.symbol,
            side=OrderSide(intent.side),
            quantity=decision.quantity,
            order_type=order_type,
            limit_price=limit_price,
        )
        now = ctx.now
        with self.db.session() as s:
            row = Order(
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                side=req.side.value,
                order_type=req.order_type.value,
                quantity=req.quantity,
                limit_price=limit_price,
                status=OrderStatus.PENDING.value,
                reason=intent.reason[:256],
                strategy_id=strategy_id,
                run_id=run_id,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            s.flush()
            oid = row.id
        self.hub.audit(
            "ORDER",
            f"{req.side.value} {req.quantity}주 주문 생성",
            req.symbol,
            order_id=oid,
            reason=intent.reason,
            source=source,
        )
        result = await self.broker.place_order(req)
        order = self.apply(oid, result)
        return order, decision

    # ------------------------------------------------------------ 상태 반영
    def apply(self, order_id: int, result: OrderResult) -> Order:
        with self.db.session() as s:
            o = s.get(Order, order_id)
            assert o is not None
            prev = OrderStatus(o.status)
            path = result.path or ([result.status] if result.status != prev else [])
            new = walk(prev, path)
            o.status = new.value
            o.broker_order_id = result.broker_order_id
            o.fill_basis = result.fill_basis or o.fill_basis
            o.trade_day = result.trade_day or o.trade_day
            o.reject_reason = result.reject_reason
            o.filled_qty = result.filled_qty
            o.avg_fill_price = result.avg_fill_price
            o.updated_at = datetime.now(UTC)
            for f in result.fills:
                s.add(
                    Fill(
                        order_id=o.id,
                        symbol=o.symbol,
                        side=o.side,
                        quantity=f.quantity,
                        price=f.price,
                        fee=f.fee,
                        tax=f.tax,
                        filled_at=f.filled_at,
                    )
                )
            s.flush()
            snapshot = order_dict(o)
            s.expunge(o)
        self._announce(snapshot, prev, new, result)
        return o

    def _announce(self, o: dict[str, Any], prev: OrderStatus, new: OrderStatus, r: OrderResult) -> None:
        sym, side = self._label(o["symbol"]), _side_ko(o["side"])
        if new == prev and not r.fills:
            return
        self.hub.audit(new.value, f"{o['side']} {o['quantity']}주 → {new.value}", o["symbol"], order=o)
        if new == OrderStatus.SUBMITTED:
            when = "오늘 시가" if o["fill_basis"] == "OPEN" else "오늘 종가"
            self.hub.chat(
                BROKER,
                "text",
                f"📝 {sym} {side} {o['quantity']}주 접수 완료! {when} 기준으로 체결될 예정이에요.",
                symbol=o["symbol"],
                order=o,
            )
        elif new == OrderStatus.REJECTED:
            self.hub.chat(
                BROKER,
                "text",
                f"🙅 {sym} {side} 주문이 거부됐어요: "
                f"{_REJECT_KO.get(o['reject_reason'] or '', o['reject_reason'])}",
                symbol=o["symbol"],
                order=o,
            )
        for f in r.fills:
            emoji = "💸" if o["side"] == "SELL" else "🛒"
            self.hub.chat(
                BROKER,
                "fill",
                f"{emoji} 체결! {sym} {side} {f.quantity}주 @ {f.price:,.0f}원 "
                f"(수수료 {f.fee:,.0f}원{', 세금 ' + format(f.tax, ',.0f') + '원' if f.tax else ''})",
                symbol=o["symbol"],
                order=o,
                fill={"quantity": f.quantity, "price": f.price, "fee": f.fee, "tax": f.tax},
            )
            self.hub.audit(
                "FILL",
                f"{o['side']} {f.quantity}주 @ {f.price:,.2f}",
                o["symbol"],
                order_id=o["id"],
                price=f.price,
                quantity=f.quantity,
            )
        if new == OrderStatus.CANCELED and prev != OrderStatus.CANCELED:
            self.hub.chat(
                BROKER,
                "text",
                f"↩️ {sym} {side} 주문 정리: "
                f"{_REJECT_KO.get(o['reject_reason'] or '', o['reject_reason'] or '취소')}",
                symbol=o["symbol"],
                order=o,
            )

    # ------------------------------------------------------------ 체결 확정 / 취소
    def settle(self) -> int:
        if not isinstance(self.broker, PaperBroker):
            return 0
        results = self.broker.settle()
        n = 0
        for r in results:
            with self.db.session() as s:
                oid = s.scalar(select(Order.id).where(Order.broker_order_id == r.broker_order_id))
            if oid is not None:
                self.apply(oid, r)
                n += 1
        return n

    async def cancel(self, order_id: int, reason: str = "canceled-by-user") -> Order | None:
        with self.db.session() as s:
            o = s.get(Order, order_id)
            if o is None or OrderStatus(o.status) in TERMINAL or o.broker_order_id is None:
                return None
            bid = o.broker_order_id
        r = await self.broker.cancel_order(bid)
        r.reject_reason = reason
        return self.apply(order_id, r)

    async def cancel_all(self, reason: str = "kill-switch") -> int:
        with self.db.session() as s:
            ids = list(s.scalars(select(Order.id).where(Order.status.in_(["SUBMITTED", "PARTIALLY_FILLED"]))))
        n = 0
        for i in ids:
            if await self.cancel(i, reason):
                n += 1
        return n


def _side_ko(side: str) -> str:
    return "매수" if side == "BUY" else "매도"
