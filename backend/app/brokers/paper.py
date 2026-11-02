"""자체 모의계좌(PaperBroker).

체결 규칙(docs/DATA_API_NOTES.md §5):
- 08:30~09:00 접수 → 당일 시가 체결, 09:00~15:20 접수 → 당일 종가 체결, 그 외 → 거부
- 공식 일봉이 공개되면(T+1 13:00) `settle()` 이 체결을 확정한다
- 체결 수량 ≤ 당일 거래량 × 참여율. 남은 수량은 부분체결 후 취소
- 수수료·매도세·슬리피지 반영
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from app.brokers.base import (
    TERMINAL,
    Balance,
    BrokerInterface,
    FillInfo,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from app.core.clock import KST, Clock
from app.core.http import LiveOrderBlockedError
from app.core.market_calendar import MarketCalendar
from app.db.models import PaperAccountState
from app.db.session import Database
from app.market.store import PriceStore
from app.market.types import Bar

log = logging.getLogger(__name__)

BUY_RESERVE_BUFFER = 0.05  # 시가 갭 상승 대비 예약 여유


@dataclass(frozen=True)
class PaperCosts:
    commission_rate: float = 0.00015
    sell_tax_rate: float = 0.0020
    slippage_bps: float = 5.0
    max_volume_participation: float = 0.05


class QuoteSource(Protocol):
    """실시간 현재가 공급자 (토스 시세)."""

    async def last_prices(self, symbols: list[str]) -> dict[str, float]: ...


class PaperBroker(BrokerInterface):
    """가상 계좌.

    - quotes 가 없으면 '다음 공식 일봉' 체결 (Mock·공공데이터, D-05)
    - quotes 가 있으면 실시간 체결 (토스 시세, D-24): 장중 시장가는 현재가로 즉시, 지정가는 가격 도달 시,
      장 시작 전 주문은 09:00 이후 첫 현재가로, 당일 15:20 까지 미체결분은 취소
    """

    is_paper = True

    def __init__(
        self,
        db: Database,
        store: PriceStore,
        calendar: MarketCalendar,
        clock: Clock,
        *,
        initial_cash: float,
        costs: PaperCosts | None = None,
        trading_mode: str = "paper",
        persist: bool = True,
        quotes: QuoteSource | None = None,
    ) -> None:
        if trading_mode != "paper":
            raise LiveOrderBlockedError("PaperBroker 는 TRADING_MODE=paper 에서만 생성할 수 있습니다.")
        self.db = db
        self.store = store
        self.cal = calendar
        self.clock = clock
        self.costs = costs or PaperCosts()
        self.persist = persist
        self.trading_mode = trading_mode
        self.quotes = quotes
        self.state: dict[str, Any] = self._load() if persist else {}
        if not self.state:
            self.state = self._fresh(initial_cash)
            self._save()

    # ----------------------------------------------------------- 영속화
    @staticmethod
    def _fresh(initial_cash: float) -> dict[str, Any]:
        return {
            "initial_cash": initial_cash,
            "cash": initial_cash,
            "positions": {},
            "orders": {},
            "client_ids": {},
            "seq": 0,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "taxes_paid": 0.0,
        }

    def _load(self) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(PaperAccountState, 1)
            return dict(row.state) if row else {}

    def _save(self) -> None:
        if not self.persist:
            return
        with self.db.session() as s:
            row = s.get(PaperAccountState, 1)
            if row is None:
                s.add(PaperAccountState(id=1, state=self.state))
            else:
                row.state = dict(self.state)

    def reset(self, initial_cash: float | None = None) -> None:
        self.state = self._fresh(initial_cash or self.state["initial_cash"])
        self._save()

    # ----------------------------------------------------------- 안전 가드
    def _guard(self) -> None:
        if not self.is_paper or self.trading_mode != "paper":
            raise LiveOrderBlockedError("모의투자(paper) 외의 주문은 허용되지 않습니다.")

    # ----------------------------------------------------------- 조회
    @property
    def realtime(self) -> bool:
        return self.quotes is not None

    async def _live_prices(self, symbols: list[str]) -> dict[str, float]:
        if self.quotes is None or not symbols:
            return {}
        try:
            return await self.quotes.last_prices(symbols)
        except Exception as e:  # 시세 일시 장애 → 마지막 일봉으로 대체
            log.warning("실시간 시세 조회 실패: %s", e)
            return {}

    async def get_quote(self, symbol: str) -> Quote | None:
        b = self.store.last_bar(symbol)
        px = (await self._live_prices([symbol])).get(symbol)
        if px:
            return Quote(symbol, px, self.clock.now().astimezone(KST).date(), b.name if b else "")
        return Quote(symbol, b.close, b.day, b.name) if b else None

    async def get_candles(self, symbol: str, n: int = 120) -> list[object]:
        return list(self.store.history(symbol, n=n))

    def _reserved(self) -> float:
        return sum(
            o.get("reserved", 0.0) for o in self.state["orders"].values() if o["status"] not in TERMINAL
        )

    def _pending_qty(self, symbol: str, side: OrderSide) -> int:
        return sum(
            o["quantity"] - o["filled_qty"]
            for o in self.state["orders"].values()
            if o["symbol"] == symbol and o["side"] == side and o["status"] not in TERMINAL
        )

    async def get_positions(self) -> list[Position]:
        out = []
        live = await self._live_prices([s for s, p in self.state["positions"].items() if p["qty"] > 0])
        today = self.clock.now().astimezone(KST).date()
        for sym, p in self.state["positions"].items():
            if p["qty"] <= 0:
                continue
            b = self.store.last_bar(sym)
            if sym in live:
                out.append(
                    Position(
                        symbol=sym,
                        name=(b.name if b else p.get("name", "")),
                        quantity=int(p["qty"]),
                        avg_price=float(p["avg_price"]),
                        last_price=live[sym],
                        price_day=today,
                        opened_day=date.fromisoformat(p["opened_day"]) if p.get("opened_day") else None,
                    )
                )
                continue
            out.append(
                Position(
                    symbol=sym,
                    name=(b.name if b else p.get("name", "")),
                    quantity=int(p["qty"]),
                    avg_price=float(p["avg_price"]),
                    last_price=float(b.close if b else p["avg_price"]),
                    price_day=b.day if b else None,
                    opened_day=date.fromisoformat(p["opened_day"]) if p.get("opened_day") else None,
                )
            )
        return sorted(out, key=lambda x: -x.market_value)

    async def get_balance(self) -> Balance:
        positions = await self.get_positions()
        pv = sum(p.market_value for p in positions)
        st = self.state
        return Balance(
            cash=st["cash"],
            reserved_cash=self._reserved(),
            positions_value=pv,
            equity=st["cash"] + pv,
            initial_cash=st["initial_cash"],
            realized_pnl=st["realized_pnl"],
            fees_paid=st["fees_paid"],
            taxes_paid=st["taxes_paid"],
        )

    def open_orders(self) -> list[dict[str, Any]]:
        return [
            dict(o, broker_order_id=k) for k, o in self.state["orders"].items() if o["status"] not in TERMINAL
        ]

    # ----------------------------------------------------------- 주문
    def _result(
        self, oid: str, path: list[OrderStatus] | None = None, new_fills: list[FillInfo] | None = None
    ) -> OrderResult:
        o = self.state["orders"][oid]
        return OrderResult(
            broker_order_id=oid,
            client_order_id=o["client_order_id"],
            status=OrderStatus(o["status"]),
            filled_qty=o["filled_qty"],
            avg_fill_price=o.get("avg_fill_price"),
            fills=new_fills or [],
            reject_reason=o.get("reject_reason"),
            fill_basis=o.get("fill_basis", ""),
            trade_day=date.fromisoformat(o["trade_day"]) if o.get("trade_day") else None,
            path=path or [],
        )

    def _new_order(self, req: OrderRequest, status: OrderStatus, **extra: Any) -> str:
        self.state["seq"] += 1
        oid = f"P{self.state['seq']:08d}"
        self.state["orders"][oid] = {
            "client_order_id": req.client_order_id,
            "symbol": req.symbol,
            "side": req.side.value,
            "order_type": req.order_type.value,
            "quantity": req.quantity,
            "limit_price": req.limit_price,
            "status": status.value,
            "filled_qty": 0,
            "avg_fill_price": None,
            "created_at": self.clock.now().isoformat(),
            **extra,
        }
        self.state["client_ids"][req.client_order_id] = oid
        return oid

    def _reject(self, req: OrderRequest, reason: str) -> OrderResult:
        oid = self._new_order(req, OrderStatus.REJECTED, reject_reason=reason)
        self._save()
        return self._result(oid, [OrderStatus.REJECTED])

    async def place_order(self, req: OrderRequest) -> OrderResult:
        self._guard()
        existing = self.state["client_ids"].get(req.client_order_id)
        if existing:  # 멱등: 같은 client_order_id 는 같은 결과
            return self._result(existing)
        now = self.clock.now()
        basis = self.cal.fill_basis(now)
        if basis is None:
            return self._reject(req, "order-hours-closed")
        if req.quantity <= 0:
            return self._reject(req, "invalid-quantity")
        if req.order_type == OrderType.LIMIT and (req.limit_price is None or req.limit_price <= 0):
            return self._reject(req, "invalid-limit-price")
        reserved = 0.0
        if req.side == OrderSide.BUY:
            q = await self.get_quote(req.symbol)
            if q is None:
                return self._reject(req, "no-reference-price")
            ref = req.limit_price if req.order_type == OrderType.LIMIT and req.limit_price else q.price
            reserved = ref * req.quantity * (1 + BUY_RESERVE_BUFFER) * (1 + self.costs.commission_rate)
            if reserved > self.state["cash"] - self._reserved():
                return self._reject(req, "insufficient-buying-power")
        else:
            held = int(self.state["positions"].get(req.symbol, {}).get("qty", 0))
            if req.quantity > held - self._pending_qty(req.symbol, OrderSide.SELL):
                return self._reject(req, "insufficient-sellable-quantity")
        if self.realtime and basis == "CLOSE":
            basis = "NOW"  # 장중: 현재가 즉시 체결
        oid = self._new_order(
            req,
            OrderStatus.SUBMITTED,
            fill_basis=basis,
            trade_day=now.astimezone(KST).date().isoformat(),
            reserved=reserved,
        )
        if basis == "NOW":
            o = self.state["orders"][oid]
            px = (await self._live_prices([req.symbol])).get(req.symbol)
            fills = self._fill_now(o, px, now) if px else []
            self._save()
            if fills:
                return self._result(oid, [OrderStatus.SUBMITTED, OrderStatus.FILLED], fills)
            return self._result(oid, [OrderStatus.SUBMITTED])
        self._save()
        return self._result(oid, [OrderStatus.SUBMITTED])

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        self._guard()
        o = self.state["orders"].get(broker_order_id)
        if o is None:
            raise KeyError(broker_order_id)
        if o["status"] in TERMINAL:
            return self._result(broker_order_id)
        o["status"] = OrderStatus.CANCELED.value
        o["reserved"] = 0.0
        o["reject_reason"] = "canceled-by-user"
        self._save()
        return self._result(broker_order_id, [OrderStatus.CANCELED])

    async def get_order_status(self, broker_order_id: str) -> OrderResult:
        return self._result(broker_order_id)

    # ----------------------------------------------------------- 체결 확정
    def _fill_price(self, o: dict[str, Any], bar: Bar) -> float | None:
        side, basis = o["side"], o["fill_basis"]
        is_limit = o["order_type"] == OrderType.LIMIT.value
        limit = float(o.get("limit_price") or 0.0)
        if basis == "OPEN":
            ref = bar.open
            if not is_limit:
                return ref
            if side == "BUY":
                return ref if ref <= limit else (limit if bar.low <= limit else None)
            return ref if ref >= limit else (limit if bar.high >= limit else None)
        ref = bar.close
        if not is_limit:
            return ref
        if side == "BUY":
            return ref if ref <= limit else None
        return ref if ref >= limit else None

    def _fill_now(self, o: dict[str, Any], px: float | None, now: datetime) -> list[FillInfo]:
        """실시간 현재가로 체결 시도. 지정가 미도달이면 빈 리스트."""
        if not px or px <= 0:
            return []
        if o["order_type"] == OrderType.LIMIT.value:
            limit = float(o.get("limit_price") or 0.0)
            if (o["side"] == "BUY" and px > limit) or (o["side"] == "SELL" and px < limit):
                return []
        slip = self.costs.slippage_bps / 10_000
        fill_px = round(px * (1 + slip) if o["side"] == "BUY" else px * (1 - slip), 2)
        qty = o["quantity"] - o["filled_qty"]
        if o["side"] == "BUY":
            qty = min(
                qty, max(0, math.floor(self.state["cash"] / (fill_px * (1 + self.costs.commission_rate))))
            )
        if qty <= 0:
            return []
        o["reserved"] = 0.0
        fill = self._apply_fill(o, qty, fill_px, now)
        if o["filled_qty"] >= o["quantity"]:
            o["status"] = OrderStatus.FILLED.value
        return [fill]

    async def _settle_realtime(self) -> list[OrderResult]:
        now = self.clock.now()
        today = now.astimezone(KST).date()
        session = self.cal.session(today)
        opened = [(oid, o) for oid, o in self.state["orders"].items() if o["status"] not in TERMINAL]
        if not opened:
            return []
        prices = await self._live_prices(sorted({o["symbol"] for _, o in opened}))
        results: list[OrderResult] = []
        for oid, o in opened:
            tday = date.fromisoformat(o["trade_day"])
            if session is None or tday < today or now >= session.order_cutoff:
                o["reserved"] = 0.0
                o["status"], o["reject_reason"] = OrderStatus.CANCELED.value, "day-order-expired"
                results.append(self._result(oid, [OrderStatus.CANCELED]))
                continue
            if now < session.open:
                continue  # 장 시작 전 주문은 09:00 이후 체결
            fills = self._fill_now(o, prices.get(o["symbol"]), now)
            if fills:
                path = (
                    [OrderStatus.FILLED]
                    if o["status"] == OrderStatus.FILLED.value
                    else [OrderStatus.PARTIALLY_FILLED]
                )
                results.append(self._result(oid, path, fills))
        if results:
            self._save()
        return results

    async def settle(self) -> list[OrderResult]:
        """미체결 주문 처리. 일봉 모드: 공개된 일봉으로 확정 / 실시간 모드: 현재가로 체결·당일 만료 취소."""
        self._guard()
        if self.realtime:
            return await self._settle_realtime()
        published = self.cal.latest_published_day(self.clock.now())
        results: list[OrderResult] = []
        for oid, o in self.state["orders"].items():
            if o["status"] in TERMINAL or not o.get("trade_day"):
                continue
            tday = date.fromisoformat(o["trade_day"])
            if tday > published:
                continue
            bar = self.store.bar_on(o["symbol"], tday)
            if bar is None:
                continue  # 아직 해당 일자 시세를 저장하지 못함 → 다음 동기화 후 처리
            results.append(self._settle_one(oid, o, bar))
        if results:
            self._save()
        return results

    def _settle_one(self, oid: str, o: dict[str, Any], bar: Bar) -> OrderResult:
        path: list[OrderStatus] = []
        fills: list[FillInfo] = []
        o["reserved"] = 0.0
        raw = self._fill_price(o, bar)
        remaining = o["quantity"] - o["filled_qty"]
        cap = math.floor(bar.volume * self.costs.max_volume_participation)
        if raw is None or cap <= 0:
            o["status"] = OrderStatus.CANCELED.value
            o["reject_reason"] = "limit-not-reached" if raw is None else "no-volume"
            return self._result(oid, [OrderStatus.CANCELED])
        slip = self.costs.slippage_bps / 10_000
        px = round(raw * (1 + slip) if o["side"] == "BUY" else raw * (1 - slip), 2)
        qty = min(remaining, cap)
        if o["side"] == "BUY":
            affordable = math.floor(self.state["cash"] / (px * (1 + self.costs.commission_rate)))
            qty = min(qty, max(0, affordable))
        if qty <= 0:
            o["status"] = OrderStatus.CANCELED.value
            o["reject_reason"] = "insufficient-cash-at-fill"
            return self._result(oid, [OrderStatus.CANCELED])
        when = datetime.combine(bar.day, datetime.min.time(), KST).replace(
            hour=9 if o["fill_basis"] == "OPEN" else 15, minute=0 if o["fill_basis"] == "OPEN" else 30
        )
        fills.append(self._apply_fill(o, qty, px, when))
        if o["filled_qty"] >= o["quantity"]:
            o["status"] = OrderStatus.FILLED.value
            path.append(OrderStatus.FILLED)
        else:
            path += [OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELED]
            o["status"] = OrderStatus.CANCELED.value
            o["reject_reason"] = "partial-fill-volume-cap"
        return self._result(oid, path, fills)

    def _apply_fill(self, o: dict[str, Any], qty: int, px: float, when: datetime) -> FillInfo:
        st = self.state
        notional = qty * px
        fee = round(notional * self.costs.commission_rate, 2)
        tax = round(notional * self.costs.sell_tax_rate, 2) if o["side"] == "SELL" else 0.0
        pos = st["positions"].setdefault(o["symbol"], {"qty": 0, "avg_price": 0.0, "opened_day": None})
        if o["side"] == "BUY":
            new_qty = pos["qty"] + qty
            pos["avg_price"] = (pos["avg_price"] * pos["qty"] + notional + fee) / new_qty
            pos["qty"] = new_qty
            pos["opened_day"] = pos["opened_day"] or when.date().isoformat()
            st["cash"] -= notional + fee
        else:
            st["realized_pnl"] += (px - pos["avg_price"]) * qty - fee - tax
            pos["qty"] -= qty
            st["cash"] += notional - fee - tax
            if pos["qty"] <= 0:
                del st["positions"][o["symbol"]]
        st["fees_paid"] += fee
        st["taxes_paid"] += tax
        prev_qty = o["filled_qty"]
        o["filled_qty"] = prev_qty + qty
        o["avg_fill_price"] = ((o.get("avg_fill_price") or 0.0) * prev_qty + px * qty) / o["filled_qty"]
        return FillInfo(quantity=qty, price=px, fee=fee, tax=tax, filled_at=when)
