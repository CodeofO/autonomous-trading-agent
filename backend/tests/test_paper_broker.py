from __future__ import annotations

from datetime import date, datetime

import pytest

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import KST
from tests.helpers import bar, make_env

D_PREV = date(2026, 10, 30)  # 금
D = date(2026, 11, 2)  # 월
NO_COST = PaperCosts(commission_rate=0, sell_tax_rate=0, slippage_bps=0, max_volume_participation=1.0)


def _env(start: datetime, costs: PaperCosts = NO_COST, cash: float = 1_000_000):
    bars = [bar("AAA", D_PREV, 10_000, 10_100, 9_900, 10_000), bar("AAA", D, 10_200, 10_500, 9_800, 10_400)]
    env = make_env(start=start, cash=cash, costs=costs, bars=bars[:1])
    env.provider.bars = bars
    return env


async def _publish(env, day: date) -> None:
    env.clock.set(env.cal.publish_time(day))
    await env.store.sync_day(day)


async def test_reject_outside_hours() -> None:
    env = _env(datetime(2026, 11, 2, 8, 0, tzinfo=KST))
    r = await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    assert r.status == OrderStatus.REJECTED and r.reject_reason == "order-hours-closed"
    env.clock.set(datetime(2026, 11, 1, 10, 0, tzinfo=KST))  # 일요일
    r = await env.broker.place_order(OrderRequest("c2", "AAA", OrderSide.BUY, 10))
    assert r.reject_reason == "order-hours-closed"


async def test_open_fill_and_idempotency() -> None:
    env = _env(datetime(2026, 11, 2, 8, 40, tzinfo=KST))
    r = await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    assert r.status == OrderStatus.SUBMITTED and r.fill_basis == "OPEN" and r.trade_day == D
    again = await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    assert again.broker_order_id == r.broker_order_id
    assert len(env.broker.open_orders()) == 1
    assert await env.broker.settle() == []  # 아직 공개 전
    await _publish(env, D)
    [res] = await env.broker.settle()
    assert res.status == OrderStatus.FILLED and res.path == [OrderStatus.FILLED]
    assert res.fills[0].price == 10_200 and res.fills[0].quantity == 10
    bal = await env.broker.get_balance()
    assert bal.cash == pytest.approx(1_000_000 - 102_000)
    [pos] = await env.broker.get_positions()
    assert pos.quantity == 10 and pos.avg_price == 10_200 and pos.last_price == 10_400


async def test_close_fill_with_costs() -> None:
    costs = PaperCosts(
        commission_rate=0.001, sell_tax_rate=0.002, slippage_bps=10, max_volume_participation=1
    )
    env = _env(datetime(2026, 11, 2, 10, 0, tzinfo=KST), costs=costs)
    await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    await _publish(env, D)
    [res] = await env.broker.settle()
    px = 10_400 * 1.001
    assert res.fills[0].price == pytest.approx(round(px, 2))
    assert res.fills[0].fee == pytest.approx(round(px * 10 * 0.001, 2))
    bal = await env.broker.get_balance()
    assert bal.cash == pytest.approx(1_000_000 - px * 10 - res.fills[0].fee, abs=0.01)


async def test_sell_tax_and_realized_pnl() -> None:
    costs = PaperCosts(commission_rate=0, sell_tax_rate=0.002, slippage_bps=0, max_volume_participation=1)
    env = _env(datetime(2026, 11, 2, 8, 40, tzinfo=KST), costs=costs)
    await env.broker.place_order(OrderRequest("b", "AAA", OrderSide.BUY, 10))
    env.clock.set(datetime(2026, 11, 2, 10, 0, tzinfo=KST))
    r = await env.broker.place_order(OrderRequest("s", "AAA", OrderSide.SELL, 10))
    assert r.reject_reason == "insufficient-sellable-quantity"  # 아직 체결 전
    await _publish(env, D)
    await env.broker.settle()
    # 다음 거래일에 매도 (종가 체결)
    d3 = date(2026, 11, 3)
    env.provider.bars.append(bar("AAA", d3, 10_400, 11_000, 10_300, 11_000))
    env.clock.set(datetime(2026, 11, 3, 14, 0, tzinfo=KST))
    r = await env.broker.place_order(OrderRequest("s2", "AAA", OrderSide.SELL, 10))
    assert r.status == OrderStatus.SUBMITTED
    r = await env.broker.place_order(OrderRequest("s3", "AAA", OrderSide.SELL, 1))
    assert r.reject_reason == "insufficient-sellable-quantity"  # 대기 매도 수량 반영
    await _publish(env, d3)
    [res] = await env.broker.settle()
    assert res.fills[0].tax == pytest.approx(110_000 * 0.002)
    bal = await env.broker.get_balance()
    assert bal.realized_pnl == pytest.approx((11_000 - 10_200) * 10 - 220)
    assert await env.broker.get_positions() == []


async def test_insufficient_cash_rejected() -> None:
    env = _env(datetime(2026, 11, 2, 9, 30, tzinfo=KST), cash=100_000)
    r = await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))  # 10,000*10*1.05 > 100,000
    assert r.reject_reason == "insufficient-buying-power"
    r = await env.broker.place_order(OrderRequest("c2", "AAA", OrderSide.BUY, 9))
    assert r.status == OrderStatus.SUBMITTED
    r = await env.broker.place_order(OrderRequest("c3", "AAA", OrderSide.BUY, 1))
    assert r.reject_reason == "insufficient-buying-power"  # 예약금 반영


async def test_limit_not_reached_is_canceled() -> None:
    env = _env(datetime(2026, 11, 2, 8, 45, tzinfo=KST))
    await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 5, OrderType.LIMIT, 9_700))
    await _publish(env, D)
    [res] = await env.broker.settle()
    assert res.status == OrderStatus.CANCELED and res.reject_reason == "limit-not-reached"


async def test_limit_open_fills_at_limit_when_low_touches() -> None:
    env = _env(datetime(2026, 11, 2, 8, 45, tzinfo=KST))
    await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 5, OrderType.LIMIT, 9_900))
    await _publish(env, D)
    [res] = await env.broker.settle()
    assert res.status == OrderStatus.FILLED and res.fills[0].price == 9_900


async def test_volume_cap_partial_then_cancel() -> None:
    costs = PaperCosts(commission_rate=0, sell_tax_rate=0, slippage_bps=0, max_volume_participation=0.000005)
    env = _env(datetime(2026, 11, 2, 9, 30, tzinfo=KST), costs=costs, cash=10_000_000)
    await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 20))
    await _publish(env, D)
    [res] = await env.broker.settle()  # 거래량 1,000,000 × 0.000005 = 5주
    assert res.path == [OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELED]
    assert res.filled_qty == 5 and res.status == OrderStatus.CANCELED


async def test_cancel_releases_reservation_and_state_persists() -> None:
    env = _env(datetime(2026, 11, 2, 9, 30, tzinfo=KST))
    r = await env.broker.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    assert (await env.broker.get_balance()).reserved_cash > 0
    c = await env.broker.cancel_order(r.broker_order_id)
    assert c.status == OrderStatus.CANCELED
    assert (await env.broker.get_balance()).reserved_cash == 0
    reloaded = PaperBroker(env.db, env.store, env.cal, env.clock, initial_cash=1)
    assert reloaded.state["orders"][r.broker_order_id]["status"] == "CANCELED"
    assert reloaded.state["initial_cash"] == 1_000_000
