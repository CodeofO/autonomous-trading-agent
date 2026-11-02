"""고정 데이터로 백테스트 지표 재현 (손계산 값과 일치해야 한다)."""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.engine import run_backtest
from app.brokers.paper import PaperCosts
from app.core.market_calendar import MarketCalendar
from tests.helpers import bar

CAL = MarketCalendar()
DAYS = CAL.trading_days(date(2026, 11, 2), date(2026, 11, 13))  # 10 거래일
CLOSES = [10_000, 10_000, 10_000, 9_000, 12_000, 13_000, 14_000, 10_000, 8_000, 8_000]
BARS = [bar("AAA", d, c, c, c, c) for d, c in zip(DAYS, CLOSES, strict=True)]
NO_COST = PaperCosts(commission_rate=0, sell_tax_rate=0, slippage_bps=0, max_volume_participation=1)


async def test_ma_cross_backtest_metrics_reproduce_hand_calculation() -> None:
    """
    MA2/MA3, 결정은 D 14:00 (D-1 까지 공개), 체결은 D 종가.
    - D5(11/9) 결정: bar0..4 에서 골든크로스 → 수량 floor(1,000,000×0.1 / (12,000×1.05)) = 7주, D5 종가 13,000 매수
    - D8(11/12) 결정: bar7 종가 10,000 → 손절(-23%) 매도, D8 종가 8,000 체결 (데드크로스 매도는 중복 주문으로 차단)
    - 손익 = 7 × (8,000 − 13,000) = −35,000 → 최종 965,000 (−3.5%)
    - 자산곡선 고점 1,007,000 (D7, bar6=14,000 평가) → MDD = 965,000/1,007,000 − 1 = −4.1708%
    """
    assert len(DAYS) == 10
    r = await run_backtest(
        BARS,
        "ma_cross",
        {"short": 2, "long": 3},
        ["AAA"],
        DAYS[0],
        DAYS[-1],
        initial_cash=1_000_000,
        position_size=0.1,
        costs=NO_COST,
        calendar=CAL,
    )
    assert r.final_equity == pytest.approx(965_000)
    assert r.total_return_pct == pytest.approx(-3.5)
    assert r.mdd_pct == pytest.approx((965_000 / 1_007_000 - 1) * 100, abs=1e-3)
    assert r.win_rate_pct == 0.0
    assert r.trades == 2 and r.round_trips == 1
    assert r.risk_blocks == 1
    assert [(t["side"], t["quantity"], t["price"], t["day"]) for t in r.trades_list] == [
        ("BUY", 7, 13_000, "2026-11-09"),
        ("SELL", 7, 8_000, "2026-11-12"),
    ]
    eq = {c["day"]: c["equity"] for c in r.equity_curve}
    assert eq["2026-11-02"] == 1_000_000
    assert eq["2026-11-11"] == 1_007_000
    assert eq["2026-11-12"] == 979_000


async def test_backtest_costs_and_no_lookahead() -> None:
    costs = PaperCosts(commission_rate=0.001, sell_tax_rate=0.002, slippage_bps=0, max_volume_participation=1)
    r = await run_backtest(
        BARS,
        "ma_cross",
        {"short": 2, "long": 3},
        ["AAA"],
        DAYS[0],
        DAYS[-1],
        initial_cash=1_000_000,
        position_size=0.1,
        costs=costs,
        calendar=CAL,
    )
    buy_cost = 7 * 13_000 * 1.001
    sell_net = 7 * 8_000 * (1 - 0.001 - 0.002)
    assert r.final_equity == pytest.approx(1_000_000 - buy_cost + sell_net, abs=0.05)
    # 결정 시점에 당일 데이터를 보지 않는다: 골든크로스를 만든 bar4(11/6) 다음 거래일에 매수
    assert r.trades_list[0]["day"] == "2026-11-09"


async def test_backtest_no_signal_keeps_cash() -> None:
    flat = [bar("BBB", d, 5_000, 5_000, 5_000, 5_000) for d in DAYS]
    r = await run_backtest(flat, "rsi", {}, ["BBB"], DAYS[0], DAYS[-1], initial_cash=1_000_000, calendar=CAL)
    assert r.final_equity == 1_000_000 and r.trades == 0 and r.win_rate_pct is None and r.mdd_pct == 0
