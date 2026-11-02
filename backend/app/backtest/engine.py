"""간단 백테스터 — 실거래 경로와 **같은** 전략·리스크·PaperBroker·체결 규칙 코드를 재사용한다.

타임라인(거래일 D, 운영과 동일):
- D 13:20  D-1 공식 일봉 공개 → 미체결 주문 체결 확정 → 자산 기록
- D 14:00  D-1 까지의 데이터로 전략 신호 + 손절/익절 → 주문 접수(종가 체결 창)
- D+1 13:20  D 종가로 체결 확정
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import KST, SimulatedClock
from app.core.market_calendar import MarketCalendar
from app.db.session import Database
from app.engine.events import EventHub
from app.engine.orders import OrderManager
from app.market.store import PriceStore
from app.market.types import Bar
from app.risk.rules import OrderIntent, RiskConfig, RiskContext, RiskManager
from app.strategies.indicators import max_drawdown
from app.strategies.rules import build


class _MemProvider:
    name = "backtest"

    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    async def fetch_day(self, day: date) -> list[Bar]:
        return [b for b in self.bars if b.day == day]

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return [b for b in self.bars if b.symbol == symbol and start <= b.day <= end]


@dataclass
class BacktestResult:
    start: str
    end: str
    initial_cash: float
    final_equity: float
    total_return_pct: float
    mdd_pct: float
    win_rate_pct: float | None
    trades: int
    round_trips: int
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades_list: list[dict[str, Any]] = field(default_factory=list)
    risk_blocks: int = 0


async def run_backtest(
    bars: list[Bar],
    strategy_type: str,
    params: dict[str, Any] | None,
    symbols: list[str],
    start: date,
    end: date,
    *,
    initial_cash: float = 10_000_000,
    position_size: float = 0.1,
    risk: RiskConfig | None = None,
    costs: PaperCosts | None = None,
    calendar: MarketCalendar | None = None,
) -> BacktestResult:
    strategy = build(strategy_type, params)
    cal = calendar or MarketCalendar()
    db = Database("sqlite://")
    db.create_all()
    clock = SimulatedClock(datetime.combine(start, time(8, 0), KST))
    store = PriceStore(db, _MemProvider(bars))
    # 미래 정보 차단: 저장소에는 '현재 시각에 공개된' 일봉만 순차적으로 넣는다(평가·손절 판단도 동일)
    pending = sorted((b for b in bars if b.symbol in symbols), key=lambda x: x.day)
    loaded = 0

    def publish() -> None:
        nonlocal loaded
        cutoff = cal.latest_published_day(clock.now())
        n = loaded
        while n < len(pending) and pending[n].day <= cutoff:
            n += 1
        store.upsert(pending[loaded:n])
        loaded = n

    broker = PaperBroker(db, store, cal, clock, initial_cash=initial_cash, costs=costs or PaperCosts())
    hub = EventHub(db, clock)
    rm = RiskManager(risk or RiskConfig(), cal)
    sod: dict[str, float] = {}

    def visible(sym: str) -> list[Bar]:
        # 미래 정보 차단: 현재 시각에 공개된 일봉까지만
        cutoff = cal.latest_published_day(clock.now())
        return [b for b in by_symbol.get(sym, []) if b.day <= cutoff]

    by_symbol: dict[str, list[Bar]] = {}
    for b in sorted(bars, key=lambda x: x.day):
        if b.symbol in symbols:
            by_symbol.setdefault(b.symbol, []).append(b)

    async def ctx() -> RiskContext:
        bal = await broker.get_balance()
        day = clock.now().date().isoformat()
        sod.setdefault(day, bal.equity)
        return RiskContext(
            now=clock.now(),
            balance=bal,
            positions=await broker.get_positions(),
            open_order_symbols=orders.open_symbols(),
            last_order_at=orders.last_order_times(),
            start_of_day_equity=sod[day],
        )

    orders = OrderManager(db, broker, rm, hub, ctx)
    curve: list[dict[str, Any]] = []
    blocks = 0
    for d in cal.trading_days(start, end):
        clock.set(datetime.combine(d, time(13, 20), KST))
        publish()
        orders.settle()
        bal = await broker.get_balance()
        curve.append({"day": d.isoformat(), "equity": round(bal.equity, 2), "cash": round(bal.cash, 2)})
        clock.set(datetime.combine(d, time(14, 0), KST))
        for it in rm.exit_intents(await broker.get_positions()):
            o, _ = await orders.submit(it, source="backtest-exit")
            blocks += o is None
        held = {p.symbol: p for p in await broker.get_positions()}
        for sym in symbols:
            hist = visible(sym)
            sig = strategy.evaluate(hist)
            if sig is None or not hist:
                continue
            px = hist[-1].close
            if sig.action == "BUY" and sym not in held:
                qty = math.floor(bal.equity * position_size / (px * 1.05))
                if qty > 0:
                    o, _ = await orders.submit(
                        OrderIntent(sym, "BUY", qty, px, sig.reason), source="backtest"
                    )
                    blocks += o is None
            elif sig.action == "SELL" and sym in held:
                o, _ = await orders.submit(
                    OrderIntent(sym, "SELL", held[sym].quantity, px, sig.reason), source="backtest"
                )
                blocks += o is None
    # 마지막 주문 확정 (다음 거래일 공개분까지)
    clock.set(datetime.combine(cal.next_trading_day(end), time(13, 20), KST) + timedelta(seconds=1))
    publish()
    orders.settle()
    final = await broker.get_balance()
    if curve:
        curve[-1] = {**curve[-1], "equity": round(final.equity, 2), "cash": round(final.cash, 2)}
    trades_list, pnls = _round_trips(broker)
    wins = [p for p in pnls if p > 0]
    return BacktestResult(
        start=start.isoformat(),
        end=end.isoformat(),
        initial_cash=initial_cash,
        final_equity=round(final.equity, 2),
        total_return_pct=round((final.equity / initial_cash - 1) * 100, 4),
        mdd_pct=round(max_drawdown([c["equity"] for c in curve]) * 100, 4),
        win_rate_pct=round(len(wins) / len(pnls) * 100, 2) if pnls else None,
        trades=sum(1 for o in broker.state["orders"].values() if o["filled_qty"] > 0),
        round_trips=len(pnls),
        equity_curve=curve,
        trades_list=trades_list,
        risk_blocks=blocks,
    )


def _round_trips(broker: PaperBroker) -> tuple[list[dict[str, Any]], list[float]]:
    """체결 순서대로 평균단가 방식 왕복 손익 계산 (매도 1건 = 왕복 1회)."""
    pos: dict[str, tuple[int, float]] = {}
    out, pnls = [], []
    c = broker.costs
    for o in sorted(broker.state["orders"].values(), key=lambda x: x["created_at"]):
        q, px = o["filled_qty"], o.get("avg_fill_price")
        if not q or px is None:
            continue
        qty, avg = pos.get(o["symbol"], (0, 0.0))
        if o["side"] == "BUY":
            cost = px * q * (1 + c.commission_rate)
            pos[o["symbol"]] = (qty + q, (avg * qty + cost) / (qty + q))
        else:
            proceeds = px * q * (1 - c.commission_rate - c.sell_tax_rate)
            pnl = proceeds - avg * q
            pnls.append(pnl)
            pos[o["symbol"]] = (qty - q, avg)
        out.append(
            {
                "symbol": o["symbol"],
                "side": o["side"],
                "quantity": q,
                "price": round(px, 2),
                "day": o["trade_day"],
            }
        )
    return out, pnls
