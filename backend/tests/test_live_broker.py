"""실거래(토스증권 실계좌) 경로 테스트 — 가짜 토스 서버(respx)만 쓴다. 실제 주문은 절대 나가지 않는다.

요청·응답 형식은 토스 Open API 명세(openapi.json 1.2.28)의 주문·계좌 스키마를 따른다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.toss_live import TossLiveBroker, TossTradingClient, krx_tick, round_to_tick
from app.container import LIVE_SOURCE, build_container
from app.core.config import LIVE_CONFIRM_PHRASE, Settings
from app.core.http import LiveOrderBlockedError, OutboundBlockedError, SafeHttpClient, check_outbound
from app.db.models import ChatMessage, Fill, Order
from app.risk.rules import OrderIntent
from tests.fake_toss import FakeTossClient, at
from tests.helpers import SimulatedClock, make_container

API = "https://openapi.tossinvest.com/api/v1"
D = date(2026, 11, 2)


class TokenStub:
    """시세 클라이언트 대신 토큰만 주는 가짜 (주문 클라이언트는 시세 클라이언트 토큰을 공유한다)."""

    def __init__(self) -> None:
        self.forced = 0

    async def _bearer(self, force: bool = False) -> str:
        self.forced += force
        return "tok-test"


def ok(result: Any) -> httpx.Response:
    return httpx.Response(200, json={"result": result})


def err(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"requestId": "r1", "code": code, "message": code}})


def order_row(status: str, filled: int = 0, avg: str | None = None, side: str = "BUY") -> dict[str, Any]:
    return {
        "orderId": "T-1",
        "symbol": "005930",
        "side": side,
        "orderType": "MARKET",
        "timeInForce": "DAY",
        "status": status,
        "quantity": "5",
        "currency": "KRW",
        "orderedAt": "2026-11-02T10:00:00+09:00",
        "execution": {
            "filledQuantity": str(filled),
            "averageFilledPrice": avg,
            "filledAmount": None,
            "commission": "10" if filled else None,
            "tax": None,
            "filledAt": "2026-11-02T10:00:01+09:00" if filled else None,
            "settlementDate": None,
        },
    }


def mock_account(holdings: list[dict[str, Any]] | None = None, cash: str = "5000000") -> None:
    respx.get(f"{API}/accounts").mock(
        return_value=ok([{"accountNo": "123-45", "accountSeq": 7, "accountType": "BROKERAGE"}])
    )
    respx.get(f"{API}/holdings").mock(return_value=ok({"items": holdings or []}))
    respx.get(f"{API}/buying-power").mock(return_value=ok({"currency": "KRW", "cashBuyingPower": cash}))


def live_container(tmp_path: Path, db: str = "sqlite://", create_schema: bool = True) -> Any:
    clock = SimulatedClock(at(D, 10))
    s = Settings(
        database_url=db,
        data_dir=tmp_path,
        enable_scheduler=False,
        trading_mode="live",
        live_trading_confirm=LIVE_CONFIRM_PHRASE,
        live_daily_buy_limit=1_000_000,
    )
    trading = TossTradingClient(TokenStub())  # type: ignore[arg-type]
    return build_container(
        s, create_schema=create_schema, toss_client=FakeTossClient(clock), trading_client=trading, clock=clock
    )


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.brokers.toss_live.asyncio.sleep", no_sleep)


# ------------------------------------------------------------------ 네트워크 가드
def test_trading_paths_blocked_unless_trading_client() -> None:
    from app.brokers.toss_live import TRADING_ROUTES

    with pytest.raises(LiveOrderBlockedError):
        check_outbound("POST", f"{API}/orders", TRADING_ROUTES)  # 일반 클라이언트: 항상 차단
    check_outbound("POST", f"{API}/orders", TRADING_ROUTES, allow_trading=True)
    check_outbound("POST", f"{API}/orders/T-1/cancel", TRADING_ROUTES, allow_trading=True)
    # 주문 클라이언트여도 허용목록 밖(정정·조건주문·다른 호스트)은 차단
    with pytest.raises(LiveOrderBlockedError):
        check_outbound("POST", f"{API}/orders/T-1/modify", TRADING_ROUTES, allow_trading=True)
    with pytest.raises(LiveOrderBlockedError):
        check_outbound("POST", f"{API}/conditional-orders", TRADING_ROUTES, allow_trading=True)
    with pytest.raises(OutboundBlockedError):
        check_outbound("POST", "https://evil.example.com/api/v1/orders", TRADING_ROUTES, allow_trading=True)


@respx.mock
async def test_order_post_is_never_retried_by_http_layer() -> None:
    from app.brokers.toss_live import TRADING_ROUTES

    route = respx.post(f"{API}/orders").mock(return_value=httpx.Response(503))
    client = SafeHttpClient(TRADING_ROUTES, allow_trading=True, max_retries=4, backoff_scale=0)
    r = await client.request("POST", f"{API}/orders", json={})
    assert r.status_code == 503 and route.call_count == 1  # 중복 주문 방지: 재시도 없음
    await client.aclose()


def test_live_broker_refuses_paper_mode(tmp_path: Path) -> None:
    c = make_container(tmp_path)
    with pytest.raises(LiveOrderBlockedError):
        TossLiveBroker(
            TossTradingClient(TokenStub()),  # type: ignore[arg-type]
            c.toss,
            c.store,
            c.clock,
            c.kv,
            trading_mode="paper",
            daily_buy_limit=1,
        )


def test_paper_container_has_no_trading_client(tmp_path: Path) -> None:
    c = make_container(tmp_path)
    assert c.trading is None and c.broker.is_paper and not c.broker.is_live


# ------------------------------------------------------------------ 호가단위
@pytest.mark.parametrize(
    ("price", "tick"),
    [(1_999, 1), (2_000, 5), (19_990, 10), (49_950, 50), (70_000, 100), (300_000, 500), (900_000, 1_000)],
)
def test_krx_tick(price: int, tick: int) -> None:
    assert krx_tick(price) == tick


def test_round_to_tick_direction() -> None:
    assert round_to_tick(70_050, OrderSide.BUY) == 70_000
    assert round_to_tick(70_050, OrderSide.SELL) == 70_100


# ------------------------------------------------------------------ 주문
@respx.mock
async def test_market_buy_request_matches_spec_and_fills(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    create = respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1", "clientOrderId": "x"}))
    respx.get(f"{API}/orders/T-1").mock(return_value=ok(order_row("FILLED", 5, "70100")))
    res = await c.broker.place_order(OrderRequest("cid-1", "005930", OrderSide.BUY, 5))
    req = create.calls[0].request
    body = json.loads(req.content)
    assert req.headers["X-Tossinvest-Account"] == "7" and req.headers["Authorization"] == "Bearer tok-test"
    assert body == {
        "clientOrderId": "cid-1",
        "symbol": "005930",
        "side": "BUY",
        "orderType": "MARKET",
        "timeInForce": "DAY",
        "quantity": "5",
    }  # 시장가는 price 를 보내지 않는다, 숫자는 문자열
    assert res.status == OrderStatus.FILLED and res.path == [OrderStatus.SUBMITTED, OrderStatus.FILLED]
    assert res.cumulative and res.fills[0].quantity == 5 and res.fills[0].price == 70_100


@respx.mock
async def test_limit_order_sends_tick_rounded_price(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    create = respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    respx.get(f"{API}/orders/T-1").mock(return_value=ok(order_row("PENDING")))
    res = await c.broker.place_order(
        OrderRequest("cid-2", "005930", OrderSide.SELL, 2, OrderType.LIMIT, 70_050)
    )
    assert json.loads(create.calls[0].request.content)["price"] == "70100"
    assert res.status == OrderStatus.SUBMITTED


@respx.mock
async def test_toss_rejection_becomes_rejected(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    respx.post(f"{API}/orders").mock(return_value=err(422, "insufficient-buying-power"))
    res = await c.broker.place_order(OrderRequest("cid-3", "005930", OrderSide.BUY, 1))
    assert res.status == OrderStatus.REJECTED and res.reject_reason == "insufficient-buying-power"


@respx.mock
async def test_unknown_submit_state_retries_with_same_client_order_id(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    create = respx.post(f"{API}/orders").mock(
        side_effect=[httpx.Response(500, json={}), ok({"orderId": "T-1"})]
    )
    respx.get(f"{API}/orders/T-1").mock(return_value=ok(order_row("PENDING")))
    res = await c.broker.place_order(OrderRequest("cid-4", "005930", OrderSide.BUY, 1))
    ids = {json.loads(call.request.content)["clientOrderId"] for call in create.calls}
    assert create.call_count == 2 and ids == {"cid-4"} and res.broker_order_id == "T-1"


@respx.mock
async def test_unknown_submit_state_after_retries_is_reported(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    respx.post(f"{API}/orders").mock(return_value=httpx.Response(500, json={}))
    res = await c.broker.place_order(OrderRequest("cid-5", "005930", OrderSide.BUY, 1))
    assert res.status == OrderStatus.REJECTED and res.reject_reason == "unknown-submit-state"


@respx.mock
async def test_daily_buy_limit_and_high_value_blocked_before_sending(tmp_path: Path) -> None:
    c = live_container(tmp_path)  # 하루 매수 상한 100만원
    mock_account()
    create = respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    res = await c.broker.place_order(OrderRequest("cid-6", "005930", OrderSide.BUY, 1_000))
    assert res.reject_reason == "live-daily-buy-limit"
    c.broker.daily_buy_limit = 1e12
    res = await c.broker.place_order(OrderRequest("cid-7", "005930", OrderSide.BUY, 2_000))  # ≈1.4억
    assert res.reject_reason == "high-value-order-blocked" and not create.called


@respx.mock
async def test_account_must_be_unambiguous(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    respx.get(f"{API}/accounts").mock(
        return_value=ok(
            [
                {"accountNo": "1", "accountSeq": 1, "accountType": "BROKERAGE"},
                {"accountNo": "2", "accountSeq": 2, "accountType": "BROKERAGE"},
            ]
        )
    )
    with pytest.raises(Exception, match="TOSS_ACCOUNT_SEQ"):
        await c.broker.ensure_account()


# ------------------------------------------------------------------ 엔진 통합
@respx.mock
async def test_engine_live_order_partial_then_filled_records_delta_fills(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account(
        holdings=[
            {
                "symbol": "000660",
                "name": "SK하이닉스",
                "marketCountry": "KR",
                "currency": "KRW",
                "quantity": "2",
                "lastPrice": "200000",
                "averagePurchasePrice": "190000",
            }
        ]
    )
    respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    status = respx.get(f"{API}/orders/T-1").mock(
        side_effect=[
            ok(order_row("PARTIAL_FILLED", 2, "70000")),
            ok(order_row("FILLED", 5, "70060")),
        ]
    )
    order, _ = await c.engine.orders.submit(OrderIntent("005930", "BUY", 5, 70_000, "테스트"))
    assert order is not None and order.status == "PARTIALLY_FILLED"
    assert await c.engine.orders.settle() == 1 and status.call_count == 2
    with c.db.session() as s:
        o = s.scalar(select(Order))
        fills = list(s.scalars(select(Fill).order_by(Fill.id)))
    assert o is not None and o.status == "FILLED" and o.filled_qty == 5
    assert [f.quantity for f in fills] == [2, 3]
    assert fills[1].price == pytest.approx((5 * 70_060 - 2 * 70_000) / 3)  # 누적 평균가에서 증분 체결가 역산
    pf = await c.engine.portfolio()
    assert pf["positions"][0]["symbol"] == "000660" and pf["cash"] == 5_000_000
    chat = [m.content for m in _chat(c)]
    assert any("토스증권에 실제 주문 접수" in m for m in chat)


@respx.mock
async def test_kill_switch_cancels_live_orders(tmp_path: Path) -> None:
    c = live_container(tmp_path)
    mock_account()
    respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    respx.get(f"{API}/orders/T-1").mock(side_effect=[ok(order_row("PENDING")), ok(order_row("CANCELED"))])
    cancel = respx.post(f"{API}/orders/T-1/cancel").mock(return_value=ok({"orderId": "T-2"}))
    await c.engine.orders.submit(OrderIntent("005930", "BUY", 5, 70_000, "테스트"))
    assert await c.engine.kill("테스트") == 1 and cancel.called
    with c.db.session() as s:
        o = s.scalar(select(Order))
    assert o is not None and o.status == "CANCELED"


async def test_switch_paper_to_live_keeps_history_and_closes_open_orders(tmp_path: Path) -> None:
    db = f"sqlite:///{tmp_path}/app.db"
    paper = make_container(tmp_path, at(D, 8, 40), database_url=db)
    order, _ = await paper.engine.orders.submit(OrderIntent("005930", "BUY", 1, 70_000, "장전 주문"))
    assert order is not None and order.status == "SUBMITTED"  # 09:00 체결 대기
    live = live_container(tmp_path, db=db, create_schema=False)
    assert live.kv.get("account_source")["source"] == LIVE_SOURCE
    with live.db.session() as s:
        o = s.scalar(select(Order))
    assert o is not None and o.status == "CANCELED" and o.reject_reason == "account-switch"
    assert any("모의투자 → 실거래" in m.content for m in _chat(live))


def _chat(c: Any) -> list[ChatMessage]:
    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage).order_by(ChatMessage.id)))


@respx.mock
def test_live_api_reports_mode_and_hides_previous_mode_history(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import _migrate, create_app

    db = f"sqlite:///{tmp_path}/app.db"
    _migrate(db)  # 실제 기동처럼 마이그레이션으로 만든 DB
    paper = make_container(tmp_path, at(D, 9, 30), database_url=db, create_schema=False)
    import asyncio

    asyncio.run(paper.engine.orders.submit(OrderIntent("005930", "BUY", 1, 70_000, "모의 매수")))
    mock_account()
    clock = SimulatedClock(at(D, 10))
    s = Settings(
        database_url=db,
        data_dir=tmp_path,
        enable_scheduler=False,
        trading_mode="live",
        live_trading_confirm=LIVE_CONFIRM_PHRASE,
    )
    app = create_app(
        s,
        toss_client=FakeTossClient(clock),
        trading_client=TossTradingClient(TokenStub()),  # type: ignore[arg-type]
        clock=clock,
    )
    with TestClient(app) as client:
        sys = client.get("/api/system").json()
        assert sys["trading_mode"] == "live" and "실제 주문" in sys["disclaimer"]
        assert client.get("/api/orders").json() == []  # 모의투자 시절 주문은 목록에서 빠짐
        assert client.get("/api/fills").json() == []
        st = client.get("/api/agent/structure").json()
        assert st["trading_mode"] == "live" and any(x["name"] == "토스 실계좌" for x in st["sources"])
        assert client.get("/api/health").json()["trading_mode"] == "live"
