"""주문 관리자: 리스크 검사 → 주문 생성(멱등키) → 상태 머신 → 체결 기록 → 감사 로그/채팅."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.brokers.base import (
    TERMINAL,
    BrokerInterface,
    FillInfo,
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
    "day-order-expired": "오늘 장중에 체결되지 않아 취소됐어요",
    "kill-switch": "킬 스위치로 취소됐어요",
    "account-switch": "거래 모드(모의/실거래)가 바뀌어 정리했어요",
    # 실거래(토스증권) — 공식 오류 코드
    "price-out-of-range": "가격이 상·하한가 범위를 벗어났어요",
    "stock-restricted": "거래가 제한된 종목이에요",
    "opposite-pending-order-exists": "같은 종목에 반대 방향 미체결 주문이 있어요",
    "max-order-amount-exceeded": "토스 1회 주문 한도를 넘었어요",
    "order-limit-exceeded": "토스 주문 한도를 넘었어요",
    "investor-exchange-not-integrated": "토스 계좌의 SOR(통합 주문) 설정이 필요해요",
    "prerequisite-required": "토스에서 먼저 해야 할 절차(약관 동의 등)가 있어요",
    "account-restricted": "계좌가 거래 제한 상태예요",
    "invalid-request": "토스가 주문 형식을 거부했어요",
    "high-value-order-blocked": "1억원(미장은 원화 환산) 이상 주문은 이 시스템에서 보내지 않아요",
    "live-daily-buy-limit": "오늘 실거래 매수 총액 상한(국장 LIVE_DAILY_BUY_LIMIT·미장 US_LIVE_DAILY_BUY_LIMIT)에 닿았어요",
    "unknown-submit-state": "주문 접수 여부를 확인하지 못했어요. 토스증권 앱에서 꼭 확인해 주세요",
    "toss-rejected": "토스증권이 주문을 거부했어요",
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


log = logging.getLogger(__name__)


class OrderManager:
    def __init__(
        self,
        db: Database,
        broker: BrokerInterface,
        risk: RiskManager,
        hub: EventHub,
        context: ContextProvider,
        name_of: Callable[[str], str] | None = None,
        currency: str = "KRW",
    ) -> None:
        self.db = db
        self.currency = currency  # 체결 안내 금액 표기 (미장 USD, D-48)
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
        with self.db.session() as s:
            rows = s.execute(
                select(Order.symbol, func.max(Order.created_at))
                .where(Order.status != "REJECTED")
                .group_by(Order.symbol)
            )
            return {sym: _aware(ts) for sym, ts in rows}

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
        try:
            result = await self.broker.place_order(req)
        except Exception as e:
            # 전송 중 오류: PENDING 으로 두면 같은 종목이 '중복 주문'으로 계속 막힌다(손절 매도 포함) → 거부로 닫는다
            log.exception("주문 전송 실패: %s %s", req.symbol, req.side.value)
            result = OrderResult(
                broker_order_id="",
                client_order_id=req.client_order_id,
                status=OrderStatus.REJECTED,
                reject_reason=f"주문 전송 실패({type(e).__name__}) — 증권사에 접수됐는지 확인이 필요해요",
            )
        order = self.apply(oid, result)
        return order, decision

    def close_stale_pending(self) -> int:
        """기동 때: 전송 결과를 받지 못하고 PENDING 에 멈춘 주문(재시작이 전송과 겹침)을 거부로 닫는다. 닫은 수."""
        with self.db.session() as s:
            ids = list(s.scalars(select(Order.id).where(Order.status == OrderStatus.PENDING.value)))
        for oid in ids:
            with self.db.session() as s:
                o = s.get(Order, oid)
                assert o is not None
                coid, sym, side, qty = o.client_order_id, o.symbol, o.side, o.quantity
            self.apply(
                oid,
                OrderResult(
                    broker_order_id="",
                    client_order_id=coid,
                    status=OrderStatus.REJECTED,
                    reject_reason="서버 재시작으로 전송 결과를 받지 못했어요 — 증권사 주문 내역을 확인해 주세요",
                ),
            )
            log.warning("멈춘 주문 정리: #%s %s %s %s주", oid, sym, side, qty)
        return len(ids)

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
            if result.cumulative:  # 누적 체결 → 이미 기록된 체결을 뺀 증분만 남긴다
                result.fills = _delta_fills(
                    list(s.scalars(select(Fill).where(Fill.order_id == o.id))), result.fills
                )
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
        if o["fill_basis"] == "LIVE" and prev == OrderStatus.PENDING and new != OrderStatus.REJECTED:
            self.hub.chat(
                BROKER,
                "text",
                f"📝 {sym} {side} {o['quantity']}주 토스증권에 실제 주문 접수!",
                symbol=o["symbol"],
                order=o,
            )
        elif new == OrderStatus.SUBMITTED:
            when = {"OPEN": "장 시작 후 시가", "NOW": "현재가 도달 시", "CLOSE": "오늘 종가"}.get(
                o["fill_basis"], ""
            )
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
            if self.currency == "USD":
                money = f"@ ${f.price:,.2f} (수수료 ${f.fee:,.2f})"
            else:
                money = (
                    f"@ {f.price:,.0f}원 "
                    f"(수수료 {f.fee:,.0f}원{', 세금 ' + format(f.tax, ',.0f') + '원' if f.tax else ''})"
                )
            self.hub.chat(
                BROKER,
                "fill",
                f"{emoji} 체결! {sym} {side} {f.quantity}주 {money}",
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
                **({"currency": self.currency} if self.currency != "KRW" else {}),
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
    async def settle(self) -> int:
        if self.broker.is_live:
            return await self._sync_live()
        if not isinstance(self.broker, PaperBroker):
            return 0
        results = await self.broker.settle()
        n = 0
        for r in results:
            with self.db.session() as s:
                oid = s.scalar(select(Order.id).where(Order.broker_order_id == r.broker_order_id))
            if oid is not None:
                self.apply(oid, r)
                n += 1
        return n

    async def _sync_live(self) -> int:
        """실거래: 미체결 주문을 토스 주문 조회로 갱신 (체결·취소·거부 반영)."""
        with self.db.session() as s:
            rows = list(
                s.execute(
                    select(Order.id, Order.broker_order_id).where(
                        Order.status.in_(["SUBMITTED", "PARTIALLY_FILLED"]),
                        Order.broker_order_id.is_not(None),
                    )
                )
            )
        n = 0
        for oid, bid in rows:
            try:
                r = await self.broker.get_order_status(bid)
            except Exception as e:  # 한 건 실패가 나머지 동기화를 막지 않게
                self.hub.audit("ERROR", f"실주문 상태 조회 실패: {type(e).__name__}", None, order_id=oid)
                continue
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


def _delta_fills(recorded: list[Fill], cumulative: list[FillInfo]) -> list[FillInfo]:
    """누적 체결(수량·평균가·수수료·세금)에서 이미 기록된 체결분을 빼 새 체결 1건으로 만든다."""
    if not cumulative:
        return []
    c = cumulative[-1]
    prev_qty = sum(f.quantity for f in recorded)
    qty = c.quantity - prev_qty
    if qty <= 0:
        return []
    amount = c.quantity * c.price - sum(f.quantity * f.price for f in recorded)
    return [
        FillInfo(
            quantity=qty,
            price=amount / qty,
            fee=max(0.0, c.fee - sum(f.fee for f in recorded)),
            tax=max(0.0, c.tax - sum(f.tax for f in recorded)),
            filled_at=c.filled_at,
        )
    ]


def _side_ko(side: str) -> str:
    return "매수" if side == "BUY" else "매도"
