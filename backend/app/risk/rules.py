"""리스크 규칙. **모든 주문은 브로커로 가기 전에 여기를 통과해야 한다.**

LLM 이 무엇을 결정하든 이 하드 리밋은 코드로 강제된다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.brokers.base import Balance, Position
from app.core.market_calendar import MarketCalendar


class RiskConfig(BaseModel):
    max_position_weight: float = Field(0.20, gt=0, le=1, description="종목당 최대 비중")
    max_order_amount: float = Field(2_000_000, gt=0, description="1회 주문 최대 금액(원)")
    daily_loss_limit: float = Field(-0.03, lt=0, ge=-1, description="일일 최대 손실 → 신규 매수 중단")
    stop_loss: float = Field(-0.05, lt=0, ge=-1, description="종목별 손절")
    take_profit: float = Field(0.10, gt=0, le=10, description="종목별 익절")
    min_order_interval_minutes: int = Field(
        60, ge=0, le=7 * 24 * 60, description="동일 종목 최소 매수 간격(매도는 제외)"
    )
    max_positions: int = Field(10, ge=1, le=100, description="최대 보유 종목 수")
    min_cash_ratio: float = Field(0.05, ge=0, lt=1, description="최소 현금 비중")
    allow_new_buys: bool = True


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    ref_price: float
    reason: str = ""


@dataclass
class RiskContext:
    now: datetime
    balance: Balance
    positions: list[Position]
    open_order_symbols: set[str]
    last_order_at: dict[str, datetime]
    start_of_day_equity: float
    killed: bool = False


@dataclass
class RiskDecision:
    allowed: bool
    quantity: int
    reasons: list[str] = field(default_factory=list)  # 차단 사유
    notes: list[str] = field(default_factory=list)  # 수량 축소 등 조정 내역
    rule: str = ""


BUY_BUFFER = 1.05


class RiskManager:
    def __init__(self, config: RiskConfig, calendar: MarketCalendar) -> None:
        self.config = config
        self.cal = calendar

    def daily_pnl_pct(self, ctx: RiskContext) -> float:
        if ctx.start_of_day_equity <= 0:
            return 0.0
        return ctx.balance.equity / ctx.start_of_day_equity - 1

    def check(self, it: OrderIntent, ctx: RiskContext) -> RiskDecision:
        c = self.config

        def block(rule: str, msg: str) -> RiskDecision:
            return RiskDecision(False, 0, [msg], rule=rule)

        if ctx.killed:
            return block("kill_switch", "킬 스위치가 켜져 있어 모든 주문이 중단됐습니다")
        if self.cal.fill_basis(ctx.now) is None:
            return block("market_hours", "장 운영시간 외 주문 금지 (08:30~15:20 거래일만 접수)")
        if it.quantity <= 0 or it.ref_price <= 0:
            return block("invalid", "수량/가격이 올바르지 않습니다")
        if it.symbol in ctx.open_order_symbols:
            return block("duplicate", "같은 종목에 미체결 주문이 이미 있습니다")
        held = {p.symbol: p for p in ctx.positions}
        qty = it.quantity
        notes: list[str] = []

        if it.side == "SELL":
            pos = held.get(it.symbol)
            if pos is None or pos.quantity <= 0:
                return block("no_position", "보유하지 않은 종목은 매도할 수 없습니다")
            if qty > pos.quantity:
                notes.append(f"보유 수량 {pos.quantity}주로 축소")
                qty = pos.quantity
            return RiskDecision(True, qty, notes=notes)

        # ---- BUY ----
        # 최소 주문 간격은 매수에만 적용: 손절·비중 축소 같은 위험을 줄이는 매도를 막지 않는다 (D-33)
        last = ctx.last_order_at.get(it.symbol)
        if last is not None and ctx.now - last < timedelta(minutes=c.min_order_interval_minutes):
            return block(
                "min_interval", f"같은 종목은 {c.min_order_interval_minutes}분 안에 다시 매수할 수 없습니다"
            )
        if not c.allow_new_buys:
            return block("buys_disabled", "신규 매수가 꺼져 있습니다")
        pnl = self.daily_pnl_pct(ctx)
        if pnl <= c.daily_loss_limit:
            return block(
                "daily_loss", f"당일 손실 {pnl:.2%} ≤ 한도 {c.daily_loss_limit:.0%} → 당일 신규 매수 중단"
            )
        if it.symbol not in held and len(held) >= c.max_positions:
            return block("max_positions", f"최대 보유 종목 수({c.max_positions}) 도달")

        px = it.ref_price * BUY_BUFFER
        equity = ctx.balance.equity
        caps: list[tuple[int, str]] = []
        caps.append((math.floor(c.max_order_amount / px), f"1회 최대 주문금액 {c.max_order_amount:,.0f}원"))
        cur_val = held[it.symbol].market_value if it.symbol in held else 0.0
        room = equity * c.max_position_weight - cur_val
        caps.append((math.floor(max(room, 0) / px), f"종목당 최대 비중 {c.max_position_weight:.0%}"))
        cash_room = ctx.balance.available_cash - equity * c.min_cash_ratio
        caps.append((math.floor(max(cash_room, 0) / px), f"최소 현금비중 {c.min_cash_ratio:.0%}"))
        for cap, label in caps:
            if cap < qty:
                if cap <= 0:
                    return block("limit", f"{label} 한도로 매수 불가")
                notes.append(f"{label} 한도로 {qty}→{cap}주 축소")
                qty = cap
        return RiskDecision(True, qty, notes=notes)

    def exit_intents(self, positions: list[Position]) -> list[OrderIntent]:
        """손절/익절 대상 전량 매도 의도."""
        out = []
        for p in positions:
            if p.quantity <= 0:
                continue
            if p.pnl_pct <= self.config.stop_loss:
                out.append(
                    OrderIntent(
                        p.symbol,
                        "SELL",
                        p.quantity,
                        p.last_price,
                        f"손절 {p.pnl_pct:.2%} ≤ {self.config.stop_loss:.0%}",
                    )
                )
            elif p.pnl_pct >= self.config.take_profit:
                out.append(
                    OrderIntent(
                        p.symbol,
                        "SELL",
                        p.quantity,
                        p.last_price,
                        f"익절 {p.pnl_pct:.2%} ≥ {self.config.take_profit:.0%}",
                    )
                )
        return out
