"""미장 실거래 경로 (D-55) — 가짜 토스 서버(respx)만 쓴다. 실제 주문은 절대 나가지 않는다.

요청·응답 형식은 토스 Open API 명세(openapi.json 1.2.28): symbol = 영문 티커, price = 달러 소수 문자열,
buying-power?currency=USD, holdings items[].marketCountry/currency.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.paper import PaperBroker
from app.brokers.toss_live import TossLiveBroker, TossTradingClient, us_limit_price
from app.container import build_container
from app.core.clock import KST, SimulatedClock
from app.core.config import LIVE_CONFIRM_PHRASE, Settings
from app.db.models import Fill
from app.risk.rules import OrderIntent
from tests.fake_toss import FakeTossClient
from tests.test_live_broker import TokenStub, err, ok, order_row

API = "https://openapi.tossinvest.com/api/v1"


def kst(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def us_settings(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite://",
        "data_dir": tmp_path,
        "enable_scheduler": False,
        "us_trading_mode": "live",
        "us_live_trading_confirm": LIVE_CONFIRM_PHRASE,
    }
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


def us_live(tmp_path: Path, now: datetime | None = None, **kw: Any) -> Any:
    clock = SimulatedClock(now or kst(2026, 11, 5, 1, 0))  # 뉴욕 11/4 정규장 중
    s = us_settings(tmp_path, **kw)
    kr = build_container(s, create_schema=True, toss_client=FakeTossClient(clock), clock=clock)
    trading = TossTradingClient(TokenStub())  # type: ignore[arg-type]
    return build_container(s, market="US", shared=kr, trading_client=trading, clock=clock)


def mock_us_account(holdings: list[dict[str, Any]] | None = None, cash: str = "2500.50") -> respx.Route:
    respx.get(f"{API}/accounts").mock(
        return_value=ok([{"accountNo": "123-45", "accountSeq": 7, "accountType": "BROKERAGE"}])
    )
    respx.get(f"{API}/holdings").mock(return_value=ok({"items": holdings or []}))
    return respx.get(f"{API}/buying-power").mock(
        return_value=ok({"currency": "USD", "cashBuyingPower": cash})
    )


def us_row(status: str, filled: int = 0, avg: str | None = None) -> dict[str, Any]:
    row = order_row(status, filled, avg)
    return {**row, "symbol": "AAPL", "currency": "USD"}


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.brokers.toss_live.asyncio.sleep", no_sleep)


# ------------------------------------------------------------------ 설정
def test_us_live_needs_its_own_confirm_phrase(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="US_LIVE_TRADING_CONFIRM"):
        Settings(_env_file=None, us_trading_mode="live")  # type: ignore[call-arg]
    s = us_settings(tmp_path)
    assert s.us_is_live and not s.is_live  # 국장은 그대로 모의
    off = us_settings(tmp_path, us_market_enabled=False)
    assert not off.us_is_live  # 미장이 꺼져 있으면 실거래도 아님
    # 국장 확인 문구로는 미장이 켜지지 않는다
    with pytest.raises(ValueError, match="US_LIVE_TRADING_CONFIRM"):
        Settings(  # type: ignore[call-arg]
            _env_file=None, us_trading_mode="live", live_trading_confirm=LIVE_CONFIRM_PHRASE
        )


def test_us_limit_price_format() -> None:
    assert us_limit_price(187.456, OrderSide.BUY) == "187.45"  # 매수는 내림
    assert us_limit_price(187.451, OrderSide.SELL) == "187.46"  # 매도는 올림
    assert us_limit_price(0.123456, OrderSide.BUY) == "0.1234"  # $1 미만은 넷째 자리
    assert us_limit_price(0.123411, OrderSide.SELL) == "0.1235"
    assert us_limit_price(190.0, OrderSide.BUY) == "190.00"


# ------------------------------------------------------------------ 조립
def test_us_live_stack_uses_live_broker_and_no_pre_open(tmp_path: Path) -> None:
    us = us_live(tmp_path)
    assert isinstance(us.broker, TossLiveBroker) and us.broker.is_live and us.runner.live
    assert us.trading is us.broker.client and us.broker.country == "US" and us.broker.currency == "USD"
    assert us.cal.pre_open_window.total_seconds() == 0  # 장 시작 전 주문 없음
    assert us.cal.fill_basis(kst(2026, 11, 4, 23, 10)) is None  # 장전(23:00~23:30)에도 접수 안 함
    assert us.cal.fill_basis(kst(2026, 11, 4, 23, 30)) == "CLOSE"
    # 기본 일정: 첫 판단이 장 시작 5분 뒤 (주문을 낼 수 있는 시각)
    jobs = us.scheduler.default_jobs(datetime(2026, 11, 4).date())
    assert jobs[0] == (kst(2026, 11, 4, 23, 35), "morning")
    assert "실제 주문" in us.engine.order_window()  # 정규장 중
    off = us_live(tmp_path / "noon", kst(2026, 11, 5, 12, 0))  # 장 밖
    assert "장 시작 전 주문을 보내지 않음" in off.engine.order_window()


def test_us_live_shares_trading_client_with_kr_live(tmp_path: Path) -> None:
    clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
    s = us_settings(tmp_path, trading_mode="live", live_trading_confirm=LIVE_CONFIRM_PHRASE)
    trading = TossTradingClient(TokenStub())  # type: ignore[arg-type]
    kr = build_container(
        s, create_schema=True, toss_client=FakeTossClient(clock), trading_client=trading, clock=clock
    )
    us = build_container(s, market="US", shared=kr, clock=clock)
    assert us.trading is kr.trading is trading  # 주문 API 초당 한도는 계좌 하나 — 같은 클라이언트


def test_kr_live_alone_keeps_us_paper(tmp_path: Path) -> None:
    clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="sqlite://",
        data_dir=tmp_path,
        enable_scheduler=False,
        trading_mode="live",
        live_trading_confirm=LIVE_CONFIRM_PHRASE,
    )
    us = build_container(s, market="US", toss_client=FakeTossClient(clock), clock=clock)
    assert isinstance(us.broker, PaperBroker) and not us.runner.live and us.trading is None


# ------------------------------------------------------------------ 주문·잔고
@respx.mock
async def test_us_limit_buy_request_matches_spec(tmp_path: Path) -> None:
    us = us_live(tmp_path)
    mock_us_account()
    create = respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    respx.get(f"{API}/orders/T-1").mock(return_value=ok(us_row("PENDING")))
    res = await us.broker.place_order(
        OrderRequest("cid-us-1", "AAPL", OrderSide.BUY, 3, OrderType.LIMIT, 187.456)
    )
    body = json.loads(create.calls[0].request.content)
    assert body == {
        "clientOrderId": "cid-us-1",
        "symbol": "AAPL",
        "side": "BUY",
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "quantity": "3",
        "price": "187.45",
    }
    assert res.status == OrderStatus.SUBMITTED


@respx.mock
async def test_us_balance_uses_usd_and_us_holdings_only(tmp_path: Path) -> None:
    us = us_live(tmp_path)
    bp = mock_us_account(
        holdings=[
            {
                "symbol": "005930",
                "marketCountry": "KR",
                "currency": "KRW",
                "quantity": "10",
                "averagePurchasePrice": "70000",
                "lastPrice": "71000",
            },
            {
                "symbol": "AAPL",
                "name": "애플",
                "marketCountry": "US",
                "currency": "USD",
                "quantity": "2.5",
                "averagePurchasePrice": "180.10",
                "lastPrice": "190.20",
            },
            {
                "symbol": "TSLA",
                "marketCountry": "US",
                "currency": "USD",
                "quantity": "0.4",
                "averagePurchasePrice": "250",
                "lastPrice": "260",
            },
        ]
    )
    bal, positions = await us.broker.account()
    assert bp.calls[0].request.url.params["currency"] == "USD"
    assert [(p.symbol, p.quantity) for p in positions] == [("AAPL", 2)]  # 국장·소수점 1주 미만은 빠짐
    assert bal.cash == 2500.50 and bal.equity == pytest.approx(2500.50 + 2 * 190.20)


@respx.mock
async def test_us_high_value_converted_with_toss_fx_and_daily_limit_in_usd(tmp_path: Path) -> None:
    us = us_live(tmp_path, us_live_daily_buy_limit=1_000_000)
    mock_us_account()
    create = respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    # 400주 × $190 ≈ $76,000 × 1,380.5원 ≈ 1.05억원 → 보내지 않음
    big = await us.broker.place_order(
        OrderRequest("cid-big", "AAPL", OrderSide.BUY, 400, OrderType.LIMIT, 190)
    )
    assert big.status == OrderStatus.REJECTED and big.reject_reason == "high-value-order-blocked"
    small = us_live(tmp_path / "b", us_live_daily_buy_limit=1_000)  # 하루 $1,000
    over = await small.broker.place_order(
        OrderRequest("cid-day", "AAPL", OrderSide.BUY, 10, OrderType.LIMIT, 190)
    )
    assert over.status == OrderStatus.REJECTED and over.reject_reason == "live-daily-buy-limit"
    assert create.call_count == 0  # 둘 다 토스로 나가지 않았다


@respx.mock
async def test_us_fx_failure_uses_conservative_rate(tmp_path: Path) -> None:
    us = us_live(tmp_path, us_live_daily_buy_limit=1_000_000)
    mock_us_account()
    respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))

    async def boom() -> dict[str, Any]:
        raise httpx.ConnectError("fx down")

    us.toss.client.exchange_rate = boom
    # $60,000 × 보수적 환율 2,000원 = 1.2억 → 막힘 (실제 환율 1,380원이면 8,280만원이라 통과했을 금액)
    res = await us.broker.place_order(
        OrderRequest("cid-fx", "AAPL", OrderSide.BUY, 300, OrderType.LIMIT, 200)
    )
    assert res.reject_reason == "high-value-order-blocked"


@respx.mock
async def test_us_prerequisite_rejection_is_reported(tmp_path: Path) -> None:
    us = us_live(tmp_path)
    mock_us_account()
    respx.post(f"{API}/orders").mock(return_value=err(422, "prerequisite-required"))
    res = await us.broker.place_order(OrderRequest("cid-pre", "AAPL", OrderSide.BUY, 1))
    assert res.status == OrderStatus.REJECTED and res.reject_reason == "prerequisite-required"


@respx.mock
async def test_us_engine_live_order_fills_in_usd(tmp_path: Path) -> None:
    us = us_live(tmp_path)
    mock_us_account(cash="5000")
    respx.post(f"{API}/orders").mock(return_value=ok({"orderId": "T-1"}))
    respx.get(f"{API}/orders/T-1").mock(return_value=ok(us_row("FILLED", 5, "190.12")))
    order, decision = await us.engine.orders.submit(OrderIntent("AAPL", "BUY", 5, 190.0, "실거래 매수"))
    assert order is not None and decision.allowed
    with us.db.session() as s:
        f = s.scalars(select(Fill)).one()
        assert f.price == pytest.approx(190.12) and f.quantity == 5


# ------------------------------------------------------------------ API
@respx.mock
def test_us_live_api_reports_mode(tmp_path: Path) -> None:
    from app.main import create_app

    mock_us_account()
    clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
    app = create_app(
        us_settings(tmp_path, auth_enabled=False),
        toss_client=FakeTossClient(clock),
        trading_client=TossTradingClient(TokenStub()),  # type: ignore[arg-type]
        clock=clock,
    )
    with TestClient(app) as c:
        us = c.get("/api/us/system").json()
        assert us["trading_mode"] == "live" and us["paper_only"] is False
        assert "미국 주식 실제 주문" in us["disclaimer"]
        kr = c.get("/api/system").json()
        assert kr["trading_mode"] == "paper" and kr["paper_only"] is True
        ms = {m["code"]: m for m in c.get("/api/markets").json()}
        assert ms["US"]["trading_mode"] == "live" and ms["KR"]["trading_mode"] == "paper"
        st = c.get("/api/us/agent/structure").json()
        assert st["trading_mode"] == "live" and st["live_daily_buy_limit"] == 3000
        assert any(x["name"] == "토스 실계좌" for x in st["sources"])
