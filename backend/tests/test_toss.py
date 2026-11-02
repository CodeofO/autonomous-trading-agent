"""토스증권 조회 전용 연동 + 실시간 모의 체결 테스트.

응답 픽스처는 공식 OpenAPI(1.2.28) 문서의 예시 형태를 따른다 (실키 녹화 아님).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.paper import PaperBroker, PaperCosts
from app.container import build_container
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.core.http import LiveOrderBlockedError, SafeHttpClient
from app.core.market_calendar import MarketCalendar
from app.market import toss
from app.market.toss import TossApiError, TossClient, TossMarketProvider
from tests.helpers import bar, make_env

B = toss.BASE


def _client() -> TossClient:
    return TossClient(
        "tsck_test_id",
        "s_test_secret",
        http=SafeHttpClient(toss.ROUTES, rate_per_sec=1000, backoff_scale=0.0),
        rate_scale=100,
    )


def _token(n: int = 1) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": f"eyJtoken{n}.payload.sig", "token_type": "Bearer", "expires_in": 86400}
    )


def _err(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"requestId": "01H", "code": code, "message": code, "data": {}}}
    )


# ------------------------------------------------------------------ 인증·한도·오류
@respx.mock
async def test_token_issued_once_and_reused() -> None:
    tok = respx.post(f"{B}/oauth2/token").mock(return_value=_token())
    prices = respx.get(f"{B}/api/v1/prices").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "symbol": "005930",
                        "timestamp": "2026-11-02T09:30:00.123+09:00",
                        "lastPrice": "72000",
                        "currency": "KRW",
                    }
                ]
            },
        )
    )
    c = _client()
    assert (await c.prices(["005930"]))["005930"][0] == 72000
    await c.prices(["005930"])
    assert tok.call_count == 1 and prices.call_count == 2
    body = tok.calls[0].request.content.decode()
    assert "grant_type=client_credentials" in body and "client_id=tsck_test_id" in body
    assert prices.calls[0].request.headers["Authorization"] == "Bearer eyJtoken1.payload.sig"


@respx.mock
async def test_expired_token_is_reissued_once() -> None:
    respx.post(f"{B}/oauth2/token").mock(side_effect=[_token(1), _token(2)])
    route = respx.get(f"{B}/api/v1/prices").mock(
        side_effect=[
            _err(401, "expired-token"),
            httpx.Response(
                200, json={"result": [{"symbol": "000660", "lastPrice": "192500", "currency": "KRW"}]}
            ),
        ]
    )
    out = await _client().prices(["000660"])
    assert out["000660"][0] == 192500
    assert route.calls[1].request.headers["Authorization"] == "Bearer eyJtoken2.payload.sig"


@respx.mock
async def test_error_envelope_and_oauth_error() -> None:
    respx.post(f"{B}/oauth2/token").mock(return_value=_token())
    respx.get(f"{B}/api/v1/stocks").mock(return_value=_err(404, "stock-not-found"))
    with pytest.raises(TossApiError) as e:
        await _client().stocks(["999999"])
    assert e.value.code == "stock-not-found"
    respx.post(f"{B}/oauth2/token").mock(
        return_value=httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "Client authentication failed: client_secret",
            },
        )
    )
    with pytest.raises(TossApiError, match="invalid_client"):
        await _client().prices(["005930"])


@respx.mock
async def test_prices_batched_by_200() -> None:
    respx.post(f"{B}/oauth2/token").mock(return_value=_token())

    def handler(req: httpx.Request) -> httpx.Response:
        syms = req.url.params["symbols"].split(",")
        return httpx.Response(
            200, json={"result": [{"symbol": s, "lastPrice": "100", "currency": "KRW"} for s in syms]}
        )

    route = respx.get(f"{B}/api/v1/prices").mock(side_effect=handler)
    out = await _client().prices([f"{i:06d}" for i in range(250)])
    assert len(out) == 250 and route.call_count == 2


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/orders"),
        ("GET", "/api/v1/orders"),
        ("POST", "/api/v1/orders/1/cancel"),
        ("GET", "/api/v1/holdings"),
        ("GET", "/api/v1/accounts"),
        ("GET", "/api/v1/buying-power"),
        ("POST", "/api/v1/conditional-orders"),
        ("GET", "/api/v1/sellable-quantity"),
    ],
)
@respx.mock
async def test_toss_order_and_account_paths_always_blocked(method: str, path: str) -> None:
    route = respx.route(host=toss.HOST).mock(return_value=httpx.Response(200, json={}))
    http = SafeHttpClient(toss.ROUTES)
    with pytest.raises(LiveOrderBlockedError):
        await http.request(method, f"{B}{path}")
    assert not route.called


async def test_toss_unlisted_and_websocket_blocked() -> None:
    http = SafeHttpClient(toss.ROUTES)
    with pytest.raises(LiveOrderBlockedError):
        await http.request("GET", f"{B}/api/v1/stocks/005930/warnings")
    with pytest.raises(LiveOrderBlockedError):
        await http.request("GET", "https://openapi-ws.tossinvest.com/ws/v1")


# ------------------------------------------------------------------ 캔들·종목·랭킹·캘린더
def _candle(d: date, close: float) -> dict[str, Any]:
    return {
        "timestamp": f"{d.isoformat()}T00:00:00+09:00",
        "openPrice": str(close - 100),
        "highPrice": str(close + 200),
        "lowPrice": str(close - 300),
        "closePrice": str(close),
        "volume": "1000",
        "currency": "KRW",
    }


@respx.mock
async def test_fetch_history_paginates_and_adds_market_cap() -> None:
    respx.post(f"{B}/oauth2/token").mock(return_value=_token())
    respx.get(f"{B}/api/v1/stocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "symbol": "005930",
                        "name": "삼성전자",
                        "market": "KOSPI",
                        "sharesOutstanding": "5919637922",
                    }
                ]
            },
        )
    )
    days = [date(2026, 10, 30) - timedelta(days=i) for i in range(6)]
    pages = [
        {
            "result": {
                "candles": [_candle(d, 70000 + i) for i, d in enumerate(days[:3])],
                "nextBefore": days[3].isoformat() + "T00:00:00+09:00",
            }
        },
        {
            "result": {
                "candles": [_candle(d, 70000 + i) for i, d in enumerate(days[3:], 3)],
                "nextBefore": None,
            }
        },
    ]
    route = respx.get(f"{B}/api/v1/candles").mock(side_effect=[httpx.Response(200, json=p) for p in pages])
    p = TossMarketProvider(_client(), SimulatedClock(datetime(2026, 11, 2, 10, tzinfo=KST)))
    bars = await p.fetch_history("005930", days[-1], days[0])
    assert [b.day for b in bars] == sorted(days)
    assert bars[-1].close == 70000 and bars[-1].name == "삼성전자"
    assert bars[-1].market_cap == pytest.approx(70000 * 5919637922)
    assert route.calls[0].request.url.params["interval"] == "1d"
    assert route.calls[1].request.url.params["before"].startswith(days[3].isoformat())
    assert await p.fetch_day(date(2026, 10, 30)) == []


@respx.mock
async def test_screen_uses_rankings_filtered_to_common_stocks() -> None:
    respx.post(f"{B}/oauth2/token").mock(return_value=_token())
    rk = respx.get(f"{B}/api/v1/rankings").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "rankedAt": "2026-11-02T10:00:00+09:00",
                    "rankings": [
                        {
                            "rank": 1,
                            "symbol": "005930",
                            "currency": "KRW",
                            "price": {"lastPrice": "56500", "basePrice": "55800", "changeRate": "0.0125"},
                            "tradingVolume": "18432100",
                            "tradingAmount": "1041436650000",
                        },
                        {
                            "rank": 2,
                            "symbol": "069500",
                            "currency": "KRW",
                            "price": {"lastPrice": "40000", "basePrice": "40000", "changeRate": "0"},
                            "tradingVolume": "1",
                            "tradingAmount": "900000000000",
                        },
                        {
                            "rank": 3,
                            "symbol": "247540",
                            "currency": "KRW",
                            "price": {"lastPrice": "100000", "basePrice": "95000", "changeRate": "0.0526"},
                            "tradingVolume": "1",
                            "tradingAmount": "500000000000",
                        },
                    ],
                }
            },
        )
    )

    def all_handler(req: httpx.Request) -> httpx.Response:
        m = req.url.params["market"]
        rows = {
            "KOSPI": [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "securityType": "STOCK",
                    "isCommonShare": True,
                    "isinCode": "KR7005930003",
                }
            ],
            "KOSDAQ": [
                {
                    "symbol": "247540",
                    "name": "에코프로비엠",
                    "securityType": "STOCK",
                    "isCommonShare": True,
                    "isinCode": "KR7247540008",
                }
            ],
        }[m]
        return httpx.Response(200, json={"result": rows})

    respx.get(f"{B}/api/v1/stocks/all").mock(side_effect=all_handler)
    respx.get(f"{B}/api/v1/stocks").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"symbol": "005930", "sharesOutstanding": "5919637922"},
                    {"symbol": "247540", "sharesOutstanding": "97800000"},
                ]
            },
        )
    )
    p = TossMarketProvider(_client(), SimulatedClock(datetime(2026, 11, 2, 10, tzinfo=KST)))
    rows = await p.screen("trading_value", "ALL", 10)
    assert [r["symbol"] for r in rows] == ["005930", "247540"]  # ETF(069500) 제외
    assert rows[0]["change_pct"] == 1.25 and rows[1]["market"] == "KOSDAQ"
    assert rk.calls[0].request.url.params["type"] == "MARKET_TRADING_AMOUNT"
    assert rk.calls[0].request.url.params["excludeInvestmentCaution"] == "true"
    kosdaq = await p.screen("market_cap", "KOSDAQ", 10)
    assert [r["symbol"] for r in kosdaq] == ["247540"]
    assert (await p.search("삼성"))[0]["symbol"] == "005930"


def test_toss_calendar_overrides_holidays() -> None:
    cal = MarketCalendar()
    reg = {
        "startTime": "2026-11-02T10:00:00+09:00",
        "singlePriceAuctionStartTime": "2026-11-02T15:20:00+09:00",
        "endTime": "2026-11-02T15:30:00+09:00",
    }
    cal.apply_toss_calendar(
        {
            "today": {"date": "2026-11-02", "integrated": {"regularMarket": reg}},
            "nextBusinessDay": {"date": "2026-11-03", "integrated": None},
        }
    )
    assert not cal.is_trading_day(date(2026, 11, 3))  # 임시 휴장
    assert not cal.is_order_allowed(datetime(2026, 11, 2, 9, 30, tzinfo=KST))  # 10시 개장(지연)
    assert cal.is_order_allowed(datetime(2026, 11, 2, 10, 0, tzinfo=KST))


# ------------------------------------------------------------------ 실시간 모의 체결
class FakeQuotes:
    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = prices

    async def last_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.prices[s] for s in symbols if s in self.prices}


def _rt(start: datetime, prices: dict[str, float], slip: float = 0.0) -> tuple[Any, PaperBroker, FakeQuotes]:
    env = make_env(start=start, bars=[bar("AAA", date(2026, 10, 30), 10_000, 10_000, 10_000, 10_000)])
    q = FakeQuotes(prices)
    b = PaperBroker(
        env.db,
        env.store,
        env.cal,
        env.clock,
        initial_cash=1_000_000,
        persist=False,
        quotes=q,
        costs=PaperCosts(commission_rate=0, sell_tax_rate=0, slippage_bps=slip),
    )
    return env, b, q


async def test_realtime_market_order_fills_immediately() -> None:
    env, b, _ = _rt(datetime(2026, 11, 2, 10, 0, tzinfo=KST), {"AAA": 12_000}, slip=10)
    r = await b.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 10))
    assert r.path == [OrderStatus.SUBMITTED, OrderStatus.FILLED] and r.fill_basis == "NOW"
    assert r.fills[0].price == pytest.approx(12_012)
    [pos] = await b.get_positions()
    assert pos.last_price == 12_000 and pos.price_day == date(2026, 11, 2)
    assert (await b.get_quote("AAA")).price == 12_000  # type: ignore[union-attr]


async def test_realtime_limit_waits_then_fills_and_expires() -> None:
    env, b, q = _rt(datetime(2026, 11, 2, 10, 0, tzinfo=KST), {"AAA": 12_000})
    r = await b.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 5, OrderType.LIMIT, 11_500))
    assert r.status == OrderStatus.SUBMITTED and not r.fills
    assert await b.settle() == []  # 아직 미도달
    q.prices["AAA"] = 11_400
    [res] = await b.settle()
    assert res.status == OrderStatus.FILLED and res.fills[0].price == 11_400
    r2 = await b.place_order(OrderRequest("c2", "AAA", OrderSide.BUY, 1, OrderType.LIMIT, 1_000))
    env.clock.set(datetime(2026, 11, 2, 15, 20, tzinfo=KST))
    [exp] = await b.settle()
    assert exp.broker_order_id == r2.broker_order_id and exp.reject_reason == "day-order-expired"


async def test_realtime_preopen_order_fills_after_open() -> None:
    env, b, q = _rt(datetime(2026, 11, 2, 8, 40, tzinfo=KST), {"AAA": 12_000})
    r = await b.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 3))
    assert r.status == OrderStatus.SUBMITTED and r.fill_basis == "OPEN"
    assert await b.settle() == []  # 장 시작 전
    env.clock.set(datetime(2026, 11, 2, 9, 1, tzinfo=KST))
    q.prices["AAA"] = 12_300
    [res] = await b.settle()
    assert res.status == OrderStatus.FILLED and res.fills[0].price == 12_300


async def test_realtime_still_rejects_outside_hours() -> None:
    _, b, _ = _rt(datetime(2026, 11, 2, 16, 0, tzinfo=KST), {"AAA": 12_000})
    r = await b.place_order(OrderRequest("c1", "AAA", OrderSide.BUY, 1))
    assert r.reject_reason == "order-hours-closed"


# ------------------------------------------------------------------ 설정·조립
def test_toss_settings_resolution() -> None:
    s = Settings(toss_client_id="tsck_id", toss_client_secret="sec_value")  # type: ignore[arg-type]
    assert s.resolved_data_source == "toss" and s.resolved_clock_mode == "real"
    assert {"tsck_id", "sec_value"} <= set(s.secret_values())
    with pytest.raises(ValueError):
        _ = Settings(data_source="toss").resolved_data_source


def test_account_resets_when_switching_mock_to_toss(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'sw.db'}"
    mock = build_container(
        Settings(
            database_url=url, data_dir=tmp_path, enable_scheduler=False, sim_start="2026-11-02T10:00:00"
        ),
        create_schema=True,
    )
    mock.broker.state["cash"] = 1.0
    mock.broker.state["positions"]["MOCK01"] = {"qty": 1, "avg_price": 1.0, "opened_day": "2026-11-01"}
    mock.broker._save()
    real = build_container(
        Settings(
            database_url=url,
            data_dir=tmp_path,
            enable_scheduler=False,
            toss_client_id="tsck_id",
            toss_client_secret="sec_value",
        )
    )  # type: ignore[arg-type]
    assert real.data_source == "toss" and real.toss is not None and real.broker.realtime
    assert (
        real.broker.state["cash"] == real.settings.paper_initial_cash and real.broker.state["positions"] == {}
    )
    events = json.dumps([m.content for m in _chat(real)], ensure_ascii=False)
    assert "MOCK → TOSS" in events
    again = build_container(
        Settings(
            database_url=url,
            data_dir=tmp_path,
            enable_scheduler=False,
            toss_client_id="tsck_id",
            toss_client_secret="sec_value",
        )
    )  # type: ignore[arg-type]
    assert again.broker.state["initial_cash"] == real.broker.state["initial_cash"]  # 같은 소스면 유지


def _chat(c: Any) -> list[Any]:
    from sqlalchemy import select

    from app.db.models import ChatMessage

    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage)))
