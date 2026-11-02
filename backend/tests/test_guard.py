"""실거래 차단 가드 테스트 (절대 규칙 2)."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from app.brokers.base import OrderRequest, OrderSide
from app.brokers.paper import PaperBroker
from app.core.http import (
    BROKER_HOST_DENYLIST,
    LiveOrderBlockedError,
    OutboundBlockedError,
    Route,
    SafeHttpClient,
    check_outbound,
)
from app.market import public_data
from app.research import dart, naver
from tests.helpers import make_env

ALL_ROUTES = public_data.ROUTES + dart.ROUTES + naver.ROUTES


@pytest.mark.parametrize("host", BROKER_HOST_DENYLIST)
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_broker_hosts_always_blocked(host: str, method: str) -> None:
    with pytest.raises(LiveOrderBlockedError):
        check_outbound(method, f"https://{host}/api/v1/prices", ALL_ROUTES)


@pytest.mark.parametrize(
    "url",
    [
        "https://openapi.tossinvest.com/api/v1/orders",
        "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash",
        "https://paper-api.alpaca.markets/v2/orders",
        "https://evil.example.com/v1/orders",
        "https://apis.data.go.kr/1160100/orders",
    ],
)
def test_order_like_urls_blocked(url: str) -> None:
    with pytest.raises(LiveOrderBlockedError):
        check_outbound("POST", url, ALL_ROUTES)


def test_unknown_host_blocked_even_for_get() -> None:
    with pytest.raises(OutboundBlockedError):
        check_outbound("GET", "https://example.com/data", ALL_ROUTES)


def test_allowed_routes_pass() -> None:
    check_outbound("GET", public_data.URL, ALL_ROUTES)
    check_outbound("GET", "https://opendart.fss.or.kr/api/list.json", ALL_ROUTES)
    check_outbound("GET", naver.URL, ALL_ROUTES)
    with pytest.raises(OutboundBlockedError):  # 메서드가 다르면 차단
        check_outbound("POST", naver.URL, ALL_ROUTES)


@respx.mock
async def test_safe_client_never_sends_blocked_request() -> None:
    route = respx.post("https://openapi.tossinvest.com/api/v1/orders").mock(return_value=httpx.Response(200))
    client = SafeHttpClient([Route("openapi.tossinvest.com", "POST", re.compile(r".*"))])
    with pytest.raises(LiveOrderBlockedError):
        await client.request("POST", "https://openapi.tossinvest.com/api/v1/orders", json={"side": "BUY"})
    assert not route.called
    await client.aclose()


def test_paper_broker_requires_paper_mode() -> None:
    env = make_env()
    with pytest.raises(LiveOrderBlockedError):
        PaperBroker(env.db, env.store, env.cal, env.clock, initial_cash=1, trading_mode="live")


async def test_paper_broker_guard_trips_if_flag_tampered() -> None:
    env = make_env()
    env.broker.is_paper = False
    with pytest.raises(LiveOrderBlockedError):
        await env.broker.place_order(OrderRequest("x1", "MOCK01", OrderSide.BUY, 1))
    with pytest.raises(LiveOrderBlockedError):
        env.broker.settle()


def test_no_live_broker_implementation_exists() -> None:
    from app.brokers.base import BrokerInterface

    def all_subclasses(c: type) -> set[type]:
        return set(c.__subclasses__()).union(*[all_subclasses(s) for s in c.__subclasses__()])

    impls = all_subclasses(BrokerInterface)
    assert impls, "브로커 구현이 있어야 합니다"
    assert all(getattr(c, "is_paper", False) for c in impls)
