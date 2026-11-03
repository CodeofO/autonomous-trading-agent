"""실거래 차단 가드 테스트: 모의투자 모드에서는 주문·계좌 경로가 어디로도 나가지 않는다."""

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
from app.market import toss
from app.research import dart, naver
from tests.helpers import make_env

ALL_ROUTES = toss.ROUTES + dart.ROUTES + naver.ROUTES


@pytest.mark.parametrize("host", BROKER_HOST_DENYLIST)
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_broker_hosts_always_blocked(host: str, method: str) -> None:
    with pytest.raises(LiveOrderBlockedError):
        check_outbound(method, f"https://{host}/api/v1/internal-endpoint", ALL_ROUTES)  # 허용목록 밖 경로


@pytest.mark.parametrize(
    "url",
    [
        "https://openapi.tossinvest.com/api/v1/orders",
        "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash",
        "https://paper-api.alpaca.markets/v2/orders",
        "https://evil.example.com/v1/orders",
        "https://opendart.fss.or.kr/api/orders",
    ],
)
def test_order_like_urls_blocked(url: str) -> None:
    with pytest.raises(LiveOrderBlockedError):
        check_outbound("POST", url, ALL_ROUTES)


def test_unknown_host_blocked_even_for_get() -> None:
    with pytest.raises(OutboundBlockedError):
        check_outbound("GET", "https://example.com/data", ALL_ROUTES)


def test_allowed_routes_pass() -> None:
    check_outbound("GET", f"{toss.BASE}/api/v1/prices", ALL_ROUTES)
    check_outbound("GET", "https://opendart.fss.or.kr/api/list.json", ALL_ROUTES)
    check_outbound("GET", naver.URL, ALL_ROUTES)
    with pytest.raises(OutboundBlockedError):  # 메서드가 다르면 차단
        check_outbound("POST", naver.URL, ALL_ROUTES)
    # 국채 금리는 일봉만 허용 (투자자 매매는 코스피·코스닥만)
    check_outbound("GET", f"{toss.BASE}/api/v1/market-indicators/KR_BOND_10Y/candles", ALL_ROUTES)
    for path in ("KR_BOND_10Y/investor-trading", "KR_BOND_7Y/candles", "NASDAQ/candles"):
        with pytest.raises(OutboundBlockedError):
            check_outbound("GET", f"{toss.BASE}/api/v1/market-indicators/{path}", ALL_ROUTES)


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
        await env.broker.settle()


def test_broker_implementations_are_paper_or_guarded_live() -> None:
    """브로커는 모의계좌이거나, TRADING_MODE=live 에서만 만들 수 있는 토스 실계좌 브로커뿐이다 (D-34)."""
    from app.brokers.base import BrokerInterface
    from app.brokers.toss_live import TossLiveBroker

    def all_subclasses(c: type) -> set[type]:
        return set(c.__subclasses__()).union(*[all_subclasses(s) for s in c.__subclasses__()])

    impls = all_subclasses(BrokerInterface)
    assert impls == {PaperBroker, TossLiveBroker}
    assert PaperBroker.is_paper and not PaperBroker.is_live
    assert TossLiveBroker.is_live and not TossLiveBroker.is_paper
