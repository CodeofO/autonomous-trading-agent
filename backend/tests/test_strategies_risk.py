from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.brokers.base import Balance, OrderStatus, Position
from app.core.clock import KST
from app.core.market_calendar import MarketCalendar
from app.engine.state_machine import InvalidTransitionError, transition, walk
from app.risk.rules import OrderIntent, RiskConfig, RiskContext, RiskManager
from app.strategies.indicators import max_drawdown, rsi, sma
from app.strategies.rules import MovingAverageCross, RsiReversal, build
from tests.helpers import bar

OPEN_T = datetime(2026, 11, 2, 10, 0, tzinfo=KST)


def _bars(closes: list[float]) -> list:
    d0 = date(2026, 1, 1)
    return [bar("AAA", d0 + timedelta(days=i), c, c, c, c) for i, c in enumerate(closes)]


# ------------------------------------------------------------------ 지표/전략
def test_indicators() -> None:
    assert sma([1, 2, 3, 4], 2) == 3.5
    assert sma([1], 2) is None
    assert rsi(list(range(1, 30)), 14) == 100.0
    assert rsi(list(range(30, 1, -1)), 14) == pytest.approx(0.0)
    assert rsi([1, 2], 14) is None
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(-0.25)


def test_ma_cross_golden_and_dead() -> None:
    s = MovingAverageCross({"short": 2, "long": 4})
    golden = _bars([10, 10, 10, 10, 9, 12])
    sig = s.evaluate(golden)
    assert sig is not None and sig.action == "BUY"
    dead = _bars([10, 10, 10, 10, 11, 8])
    sig = s.evaluate(dead)
    assert sig is not None and sig.action == "SELL"
    assert s.evaluate(_bars([10] * 6)) is None
    assert s.evaluate(_bars([10] * 3)) is None  # 데이터 부족


def test_rsi_reversal_thresholds() -> None:
    s = RsiReversal({"period": 3, "lower": 30, "upper": 70})
    assert s.evaluate(_bars([10, 9, 8, 7, 6])).action == "BUY"  # type: ignore[union-attr]
    assert s.evaluate(_bars([6, 7, 8, 9, 10])).action == "SELL"  # type: ignore[union-attr]


def test_strategy_param_validation() -> None:
    with pytest.raises(ValidationError):
        MovingAverageCross({"short": 20, "long": 5})
    with pytest.raises(ValidationError):
        RsiReversal({"lower": 80, "upper": 70})
    with pytest.raises(KeyError):
        build("nope")


# ------------------------------------------------------------------ 상태 머신
def test_state_machine() -> None:
    S = OrderStatus
    assert walk(S.PENDING, [S.SUBMITTED, S.PARTIALLY_FILLED, S.PARTIALLY_FILLED, S.FILLED]) == S.FILLED
    assert walk(S.PENDING, [S.SUBMITTED, S.PARTIALLY_FILLED, S.CANCELED]) == S.CANCELED
    assert transition(S.FILLED, S.FILLED) == S.FILLED  # 멱등 재보고
    for bad in [
        (S.PENDING, S.FILLED),
        (S.FILLED, S.CANCELED),
        (S.CANCELED, S.SUBMITTED),
        (S.REJECTED, S.FILLED),
        (S.PARTIALLY_FILLED, S.SUBMITTED),
    ]:
        with pytest.raises(InvalidTransitionError):
            transition(*bad)


# ------------------------------------------------------------------ 리스크 (경계값)
def _pos(sym: str, qty: int, avg: float, last: float) -> Position:
    return Position(sym, sym, qty, avg, last, None, None)


def _ctx(
    equity: float = 10_000_000,
    cash: float | None = None,
    positions: list[Position] | None = None,
    now: datetime = OPEN_T,
    sod: float | None = None,
    **kw,
) -> RiskContext:
    positions = positions or []
    pv = sum(p.market_value for p in positions)
    cash = equity - pv if cash is None else cash
    bal = Balance(
        cash=cash,
        reserved_cash=0,
        positions_value=pv,
        equity=cash + pv,
        initial_cash=equity,
        realized_pnl=0,
        fees_paid=0,
        taxes_paid=0,
    )
    return RiskContext(
        now=now,
        balance=bal,
        positions=positions,
        open_order_symbols=kw.get("open", set()),
        last_order_at=kw.get("last", {}),
        start_of_day_equity=sod or bal.equity,
        killed=kw.get("killed", False),
    )


RM = RiskManager(RiskConfig(min_cash_ratio=0, max_order_amount=1_000_000), MarketCalendar())


def buy(qty: int, px: float = 10_000, sym: str = "AAA") -> OrderIntent:
    return OrderIntent(sym, "BUY", qty, px)


def test_max_order_amount_boundary() -> None:
    # 1회 최대 1,000,000원, 기준가×1.05 = 10,500 → 95주
    d = RM.check(buy(95), _ctx())
    assert d.allowed and d.quantity == 95 and not d.notes
    d = RM.check(buy(96), _ctx())
    assert d.allowed and d.quantity == 95 and "축소" in d.notes[0]


def test_position_weight_boundary() -> None:
    rm = RiskManager(RiskConfig(max_order_amount=1e12, min_cash_ratio=0), MarketCalendar())
    held = _pos("AAA", 100, 10_000, 10_000)  # 1,000,000 보유
    d = rm.check(buy(1000), _ctx(positions=[held]))
    # 한도 20% × 10,000,000 = 2,000,000 → 여유 1,000,000 / 10,500 = 95주
    assert d.quantity == 95
    full = _pos("AAA", 200, 10_000, 10_000)  # 정확히 20%
    d = rm.check(buy(1), _ctx(positions=[full]))
    assert not d.allowed and d.rule == "limit"


def test_daily_loss_limit_boundary() -> None:
    assert not RM.check(buy(1), _ctx(equity=9_700_000, sod=10_000_000)).allowed  # 정확히 -3%
    assert RM.check(buy(1), _ctx(equity=9_700_001, sod=10_000_000)).allowed
    sell = OrderIntent("AAA", "SELL", 1, 10_000)
    ctx = _ctx(equity=9_000_000, sod=10_000_000, positions=[_pos("AAA", 10, 10_000, 10_000)])
    assert RM.check(sell, ctx).allowed  # 손실 한도여도 매도는 허용


def test_stop_loss_take_profit_boundary() -> None:
    rm = RiskManager(RiskConfig(), MarketCalendar())
    exits = rm.exit_intents(
        [
            _pos("SL", 10, 10_000, 9_500),  # -5% 정확히 → 손절
            _pos("NO", 10, 10_000, 9_501),  # -4.99%
            _pos("TP", 10, 10_000, 11_000),  # +10% 정확히 → 익절
            _pos("NO2", 10, 10_000, 10_999),
        ]
    )
    assert {e.symbol for e in exits} == {"SL", "TP"}
    assert all(e.side == "SELL" and e.quantity == 10 for e in exits)


def test_market_hours_boundary() -> None:
    assert not RM.check(buy(1), _ctx(now=datetime(2026, 11, 2, 8, 29, 59, tzinfo=KST))).allowed
    assert RM.check(buy(1), _ctx(now=datetime(2026, 11, 2, 8, 30, tzinfo=KST))).allowed
    assert RM.check(buy(1), _ctx(now=datetime(2026, 11, 2, 15, 19, 59, tzinfo=KST))).allowed
    d = RM.check(buy(1), _ctx(now=datetime(2026, 11, 2, 15, 20, tzinfo=KST)))
    assert not d.allowed and d.rule == "market_hours"
    assert not RM.check(buy(1), _ctx(now=datetime(2026, 12, 25, 10, 0, tzinfo=KST))).allowed  # 휴장일


def test_duplicate_interval_kill_maxpos_and_sell_clamp() -> None:
    assert RM.check(buy(1), _ctx(open={"AAA"})).rule == "duplicate"
    assert RM.check(buy(1), _ctx(last={"AAA": OPEN_T - timedelta(minutes=59)})).rule == "min_interval"
    assert RM.check(buy(1), _ctx(last={"AAA": OPEN_T - timedelta(minutes=60)})).allowed
    # 매도(손절·비중 축소)는 최소 간격에 막히지 않는다
    recent = {"AAA": OPEN_T - timedelta(minutes=5)}
    assert RM.check(
        OrderIntent("AAA", "SELL", 1, 100), _ctx(last=recent, positions=[_pos("AAA", 3, 100, 90)])
    ).allowed
    assert RM.check(buy(1), _ctx(killed=True)).rule == "kill_switch"
    rm = RiskManager(RiskConfig(max_positions=2, min_cash_ratio=0), MarketCalendar())
    two = [_pos("X", 1, 100, 100), _pos("Y", 1, 100, 100)]
    assert rm.check(buy(1, sym="Z"), _ctx(positions=two)).rule == "max_positions"
    assert rm.check(buy(1, sym="X"), _ctx(positions=two)).allowed  # 기존 종목 추가매수는 허용
    d = RM.check(OrderIntent("X", "SELL", 5, 100), _ctx(positions=[_pos("X", 3, 100, 100)]))
    assert d.allowed and d.quantity == 3
    assert RM.check(OrderIntent("Q", "SELL", 1, 100), _ctx()).rule == "no_position"


def test_min_cash_ratio() -> None:
    rm = RiskManager(
        RiskConfig(min_cash_ratio=0.5, max_order_amount=1e12, max_position_weight=1), MarketCalendar()
    )
    d = rm.check(buy(1000), _ctx(equity=1_000_000))  # 현금 50% 유지 → 500,000/10,500 = 47주
    assert d.quantity == 47


def test_min_hold_days_blocks_early_sells_but_not_stop_loss() -> None:
    """최소 보유 기간 (D-64): 그 전에는 매도 금지, 손절·익절(리스크 엔진 매도)과 매수 날짜를 모르는 종목은 예외."""
    rm = RiskManager(RiskConfig(min_hold_days=30, stop_loss=-0.2, take_profit=1.0), MarketCalendar())
    opened = OPEN_T.date() - timedelta(days=10)
    held = Position("AAA", "AAA", 10, 10_000, 9_000, None, opened)
    sell = OrderIntent("AAA", "SELL", 10, 9_000)
    d = rm.check(sell, _ctx(positions=[held]))
    assert (
        not d.allowed
        and d.rule == "min_hold"
        and "30일 보유 원칙" in d.reasons[0]
        and "10일째" in d.reasons[0]
    )
    assert rm.check(OrderIntent("AAA", "SELL", 10, 9_000, forced=True), _ctx(positions=[held])).allowed
    late = Position("AAA", "AAA", 10, 10_000, 9_000, None, OPEN_T.date() - timedelta(days=30))
    assert rm.check(sell, _ctx(positions=[late])).allowed
    unknown = Position("AAA", "AAA", 10, 10_000, 9_000, None, None)
    assert rm.check(sell, _ctx(positions=[unknown])).allowed
    # 손절로 나가는 매도는 forced 로 만들어진다
    crash = Position("AAA", "AAA", 10, 10_000, 7_900, None, opened)
    exits = rm.exit_intents([crash])
    assert exits and exits[0].forced and rm.check(exits[0], _ctx(positions=[crash])).allowed
