"""미장(미국 주식) 모의투자 스택 (D-48): 캘린더·체결·스케줄·도구·API. 네트워크 없음 (가짜 토스)."""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.agent.tools import KR_ONLY_TOOLS, openai_tools
from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.paper import PaperBroker, PaperCosts
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.core.http import OutboundBlockedError, check_outbound
from app.core.market import spec_for
from app.core.us_calendar import UsMarketCalendar
from app.db.session import Database
from app.engine.scheduler import session_job_times
from app.market.store import PriceStore
from app.market.toss import BASE, ROUTES
from tests.helpers import StaticProvider
from tests.test_api import _client
from tests.test_llm_profile import FREE, PAID


def kst(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=KST)


# ====================================================================== 캘린더
def test_us_session_in_kst_est_and_edt() -> None:
    cal = UsMarketCalendar()
    est = cal.session(date(2026, 11, 4))  # 서머타임 끝난 뒤 (11/1)
    assert est is not None
    assert est.open == kst(2026, 11, 4, 23, 30)
    assert est.close == kst(2026, 11, 5, 6, 0)  # 한국 시각 자정을 넘긴다
    assert est.order_cutoff == kst(2026, 11, 5, 5, 50)
    edt = cal.session(date(2026, 10, 30))
    assert edt is not None and edt.open == kst(2026, 10, 30, 22, 30) and edt.close == kst(2026, 10, 31, 5, 0)
    # 서머타임 시작(2026-03-08) 앞뒤
    assert cal.session(date(2026, 3, 6)).open == kst(2026, 3, 6, 23, 30)  # type: ignore[union-attr]
    assert cal.session(date(2026, 3, 9)).open == kst(2026, 3, 9, 22, 30)  # type: ignore[union-attr]


def test_us_holidays_and_early_close() -> None:
    cal = UsMarketCalendar()
    for d in ("2026-11-26", "2026-12-25", "2026-07-03", "2027-03-26", "2027-06-18", "2027-12-24"):
        assert not cal.is_trading_day(date.fromisoformat(d)), d
    assert not cal.is_trading_day(date(2026, 11, 7))  # 토요일
    early = cal.session(date(2026, 11, 27))  # 추수감사절 다음 날 13:00 마감
    assert early is not None
    assert early.close == kst(2026, 11, 28, 3, 0) and early.order_cutoff == kst(2026, 11, 28, 2, 50)
    assert cal.next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)


def test_us_trading_day_crosses_kst_midnight_and_fill_basis() -> None:
    cal = UsMarketCalendar()
    t = kst(2026, 11, 5, 2, 0)  # 한국 목요일 새벽 = 뉴욕 수요일 12:00
    assert cal.local_date(t) == date(2026, 11, 4)
    assert cal.is_market_open(t) and cal.is_order_allowed(t)
    assert cal.fill_basis(kst(2026, 11, 4, 22, 59)) is None  # 장전 접수(23:00) 전
    assert cal.fill_basis(kst(2026, 11, 4, 23, 10)) == "OPEN"
    assert cal.fill_basis(kst(2026, 11, 4, 23, 45)) == "CLOSE"
    assert cal.fill_basis(kst(2026, 11, 5, 5, 55)) is None  # 마감 10분 전 이후
    assert cal.fill_basis(kst(2026, 11, 5, 12, 0)) is None
    # 금요일 장이 끝난 토요일 아침 → 다음 장은 월요일(뉴욕) 밤
    nxt = cal.next_session(kst(2026, 11, 7, 9, 0))
    assert nxt.day == date(2026, 11, 9) and nxt.open == kst(2026, 11, 9, 23, 30)


def test_toss_us_calendar_overrides() -> None:
    cal = UsMarketCalendar()
    cal.apply_toss_calendar(
        {
            "today": {
                "date": "2026-11-04",
                "dayMarket": None,
                "preMarket": None,
                "regularMarket": None,
                "afterMarket": None,
            },
            "nextBusinessDay": {
                "date": "2026-11-05",
                "regularMarket": {
                    "startTime": "2026-11-05T23:30:00+09:00",
                    "endTime": "2026-11-06T03:00:00+09:00",
                },
            },
        }
    )
    assert not cal.is_trading_day(date(2026, 11, 4))
    s = cal.session(date(2026, 11, 5))
    assert s is not None and s.close == kst(2026, 11, 6, 3, 0) and s.order_cutoff == kst(2026, 11, 6, 2, 50)


def test_market_calendar_us_route_is_allowlisted() -> None:
    check_outbound("GET", f"{BASE}/api/v1/market-calendar/US", ROUTES)
    with pytest.raises(OutboundBlockedError):
        check_outbound("GET", f"{BASE}/api/v1/market-calendar/JP", ROUTES)


# ====================================================================== 모의계좌 (달러)
class Quotes:
    def __init__(self, prices: dict[str, float]) -> None:
        self.prices = prices

    async def last_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.prices[s] for s in symbols if s in self.prices}


def _us_broker(now: datetime, prices: dict[str, float]) -> tuple[PaperBroker, SimulatedClock]:
    spec = spec_for("US", Settings(_env_file=None))  # type: ignore[call-arg]
    db = Database("sqlite://")
    db.create_all()
    clock = SimulatedClock(now)
    store = PriceStore(db, StaticProvider())
    costs = PaperCosts(
        spec.commission_rate, spec.sell_tax_rate, spec.slippage_bps, spec.max_volume_participation
    )
    broker = PaperBroker(
        db,
        store,
        UsMarketCalendar(),
        clock,
        initial_cash=spec.initial_cash,
        costs=costs,
        quotes=Quotes(prices),
        round_price=spec.round_price,
    )
    return broker, clock


async def test_us_paper_fill_usd_rounding_and_commission() -> None:
    broker, _ = _us_broker(kst(2026, 11, 5, 1, 0), {"AAPL": 190.123, "PENY": 0.52341})
    r = await broker.place_order(OrderRequest("c1", "AAPL", OrderSide.BUY, 10))
    assert r.status == OrderStatus.FILLED
    fill = r.fills[0]
    assert fill.price == round(190.123 * 1.0005, 2) == 190.22  # 슬리피지 5bp, $0.01 단위
    assert fill.fee == round(10 * 190.22 * 0.001, 2) == 1.9  # 수수료 0.1%
    assert fill.tax == 0.0
    assert r.trade_day == date(2026, 11, 4)  # 한국 새벽 1시 = 뉴욕 11/4 거래일
    bal = await broker.get_balance()
    assert bal.initial_cash == 10_000 and bal.cash == pytest.approx(10_000 - 1902.2 - 1.9)

    p = await broker.place_order(OrderRequest("c2", "PENY", OrderSide.BUY, 1000))
    assert p.fills[0].price == round(0.52341 * 1.0005, 4)  # $1 미만은 0.0001 단위

    s = await broker.place_order(OrderRequest("c3", "AAPL", OrderSide.SELL, 10))
    assert s.status == OrderStatus.FILLED and s.fills[0].tax == 0.0  # 미국: 증권거래세 없음
    assert s.fills[0].price == round(190.123 * 0.9995, 2)


async def test_us_order_hours_and_day_expiry() -> None:
    broker, clock = _us_broker(kst(2026, 11, 4, 12, 0), {"AAPL": 190.0})
    r = await broker.place_order(OrderRequest("x", "AAPL", OrderSide.BUY, 1))
    assert r.status == OrderStatus.REJECTED and r.reject_reason == "order-hours-closed"
    clock.set(kst(2026, 11, 4, 23, 10))  # 장전 접수 → 23:30 이후 첫 현재가로 체결
    r = await broker.place_order(OrderRequest("y", "AAPL", OrderSide.BUY, 1))
    assert r.status == OrderStatus.SUBMITTED and r.fill_basis == "OPEN"
    assert await broker.settle() == []  # 아직 장 시작 전
    clock.set(kst(2026, 11, 4, 23, 31))
    res = await broker.settle()
    assert res and res[0].status == OrderStatus.FILLED
    clock.set(kst(2026, 11, 5, 3, 0))
    lim = await broker.place_order(
        OrderRequest("z", "AAPL", OrderSide.BUY, 1, order_type=OrderType.LIMIT, limit_price=150.0)
    )
    assert lim.status == OrderStatus.SUBMITTED  # 지정가 미도달
    clock.set(kst(2026, 11, 5, 5, 51))  # 마감 10분 전 이후 → 당일 만료
    res = await broker.settle()
    assert res and res[0].status == OrderStatus.CANCELED and res[0].broker_order_id == lim.broker_order_id


# ====================================================================== 스케줄
def _times(d: date, interval: int = 60) -> list[tuple[str, str]]:
    s = UsMarketCalendar().session(d)
    assert s is not None
    return [(f"{t.astimezone(KST):%m-%d %H:%M}", name) for t, name in session_job_times(s, interval)]


def test_us_job_times_in_kst_est_edt_and_early_close() -> None:
    est = _times(date(2026, 11, 4))
    assert est[0] == ("11-04 23:05", "morning")
    assert [t for t, n in est if n == "hourly"] == [f"11-05 0{h}:30" for h in range(6)]
    assert est[-1] == ("11-05 06:15", "report")
    edt = _times(date(2026, 10, 30))
    assert edt[0] == ("10-30 22:05", "morning")
    assert [t for t, n in edt if n == "hourly"] == ["10-30 23:30"] + [f"10-31 0{h}:30" for h in range(5)]
    assert edt[-1] == ("10-31 05:15", "report")
    early = _times(date(2026, 11, 27))
    assert [t for t, n in early if n == "hourly"] == ["11-28 00:30", "11-28 01:30", "11-28 02:30"]
    assert early[-1] == ("11-28 03:15", "report")
    assert [n for _, n in _times(date(2026, 11, 4), 0)] == ["morning", "hourly", "report"]


async def test_us_dispatcher_fires_each_job_once(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 23, 5), auth_enabled=False) as c:
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        nxt = us.scheduler.next_runs(2)
        assert nxt[0] == {"name": "hourly", "at": "2026-11-05T00:30:00+09:00"}
        ran = await us.scheduler.tick(wait=True)
        assert ran == ["2026-11-04 23:05 morning"]
        assert await us.scheduler.tick(wait=True) == []  # 같은 작업은 한 번만
        us.clock.set(kst(2026, 11, 5, 0, 31))
        assert await us.scheduler.tick(wait=True) == ["2026-11-04 00:30 hourly"]
        us.clock.set(kst(2026, 11, 5, 0, 45))  # 유예(10분) 지나면 건너뛴다
        assert await us.scheduler.tick(wait=True) == []
        kr = c.app.state.container  # type: ignore[attr-defined]
        assert kr.scheduler.next_runs(1)[0]["at"] == "2026-11-05T08:35:00+09:00"  # 국장 일정은 그대로


# ====================================================================== 도구
def test_us_tool_list_excludes_kr_only_tools() -> None:
    kr = {t["function"]["name"] for t in openai_tools(toss=True)}
    us_tools = openai_tools(toss=True, market="US")
    us = [t["function"]["name"] for t in us_tools]
    assert kr >= KR_ONLY_TOOLS and not (KR_ONLY_TOOLS & set(us))
    assert us[-1] == "submit_decisions" and "get_market_overview" in us and "explore_sector" in us
    sym = us_tools[-1]["function"]["parameters"]["properties"]["decisions"]["items"]["properties"]["symbol"]
    assert "티커" in sym["description"]
    screen = next(t for t in us_tools if t["function"]["name"] == "screen_stocks")["function"]
    assert "NASDAQ" in screen["parameters"]["properties"]["market"]["enum"]


def test_us_tools_return_usd_data(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 5, 1, 0), auth_enabled=False) as c:
        tb = c.app.state.containers["US"].runner.tb  # type: ignore[attr-defined]

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await tb.call(name, args)

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            ov = loop.run_until_complete(call("get_market_overview", {}))
            assert set(ov["index_etfs"]) == {"QQQ", "SPY", "DIA"} and ov["usd_krw"]["rate"] == 1380.5
            assert "KOSPI" not in str(ov)
            scr = loop.run_until_complete(call("screen_stocks", {"sort_by": "trading_value"}))
            assert scr["results"] and "trading_value_musd" in scr["results"][0]
            assert all(r["symbol"].isalpha() for r in scr["results"])  # 미국 티커만 (국내 종목 없음)
            ph = loop.run_until_complete(call("get_price_history", {"symbol": "AAPL", "days": 60}))
            assert ph["currency"] == "USD" and ph["as_of"] == "2026-11-04" and "market_cap_busd" in ph
            st = loop.run_until_complete(call("get_stock_status", {"symbol": "AAPL"}))
            assert st["price_limits"] is None and st["currency"] == "USD"
            blocked = loop.run_until_complete(call("get_financials", {"symbol": "AAPL"}))
            assert "국내 주식 전용" in blocked["error"]
        finally:
            loop.close()


# ====================================================================== API
def _wait_run(c: TestClient, prefix: str, timeout: float = 20) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        runs = c.get(f"{prefix}/agent/runs").json()
        if runs and runs[0]["status"] != "RUNNING":
            return dict(runs[0])
        time.sleep(0.1)
    raise AssertionError("세션이 끝나지 않음")


def test_us_portfolio_usd_and_separate_from_kr(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 5, 1, 0), auth_enabled=False) as c:
        pf = c.get("/api/us/portfolio").json()
        assert pf["currency"] == "USD" and pf["market"] == "US" and pf["cash"] == 10_000
        assert {w["symbol"] for w in pf["watchlist"]} >= {"AAPL", "NVDA", "QQQ", "SPY"}
        assert pf["risk_limits"]["max_order_amount"] == 1500
        assert "KST" in pf["order_window"]
        assert c.post("/api/us/agent/run").status_code == 202
        assert _wait_run(c, "/api/us")["status"] == "DONE"
        us = c.get("/api/us/portfolio").json()
        assert us["positions"] and all(p["symbol"].isalpha() for p in us["positions"])
        assert us["cash"] < 10_000 and isinstance(us["cash"], float)
        fills = c.get("/api/us/fills").json()
        assert fills and all(f["price"] < 2000 for f in fills)  # 달러 가격
        assert "$" in c.get("/api/us/chat").text
        kr = c.get("/api/portfolio").json()
        assert kr["currency"] == "KRW" and kr["cash"] == 10_000_000 and kr["positions"] == []
        assert c.get("/api/orders").json() == [] and c.get("/api/agent/runs").json() == []
        sysus = c.get("/api/us/system").json()
        assert sysus["market"] == "US" and sysus["currency"] == "USD" and sysus["paper_only"] is True
        assert sysus["trading_mode"] == "paper" and "미장" in sysus["disclaimer"]
        assert c.get("/api/system").json()["currency"] == "KRW"
        st = c.get("/api/us/agent/structure").json()
        assert st["schedule"]["morning"] == "23:05" and "DART" not in {x["name"] for x in st["sources"]}
        assert c.get("/api/us/auth/status").status_code == 404  # 전역 API 는 /api 에만
        assert c.get("/api/us/market/AAPL/candles?days=30").json()["currency"] == "USD"


def test_markets_and_overview(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 5, 1, 0), auth_enabled=False) as c:
        ms = {m["code"]: m for m in c.get("/api/markets").json()}
        assert set(ms) == {"KR", "US"}
        us = ms["US"]
        assert us["currency"] == "USD" and us["timezone"] == "America/New_York" and us["enabled"]
        assert us["paper_only"] and us["open_now"] and us["llm_profile"] == "free"
        assert us["session"] == {
            "day": "2026-11-04",
            "open": "2026-11-04T23:30:00+09:00",
            "close": "2026-11-05T06:00:00+09:00",
            "order_cutoff": "2026-11-05T05:50:00+09:00",
        }
        assert us["next_session_open"] == "2026-11-05T23:30:00+09:00"
        assert ms["KR"]["open_now"] is False and ms["KR"]["next_session_open"] == "2026-11-05T09:00:00+09:00"

        c.post("/api/us/agent/run")
        _wait_run(c, "/api/us")
        ov = c.get("/api/overview").json()
        assert ov["fx"]["usdkrw"] == 1380.5
        rows = {m["code"]: m for m in ov["markets"]}
        assert rows["US"]["currency"] == "USD" and rows["US"]["positions"] >= 1
        assert rows["US"]["last_session"]["run_id"] == 1 and rows["KR"]["last_session"] is None
        assert rows["US"]["invested"] > 0 and rows["US"]["top_positions"][0]["symbol"].isalpha()
        expect = rows["KR"]["equity"] + rows["US"]["equity"] * 1380.5
        assert ov["total_krw"]["equity"] == round(expect)
        expect_pnl = rows["KR"]["pnl_today"] + rows["US"]["pnl_today"] * 1380.5
        assert ov["total_krw"]["pnl_today"] == round(expect_pnl)


def test_us_llm_profile_defaults_free_and_is_independent(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 5, 1, 0), auth_enabled=False, **PAID, **FREE) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        assert c.get("/api/llm/profile").json()["active"] == "paid" and kr.runner.llm.model == "paid-model"
        assert c.get("/api/us/llm/profile").json()["active"] == "free"
        assert us.runner.llm.model == "free-model"  # 국장이 유료여도 미장은 무료
        assert c.get("/api/us/system").json()["llm"]["profile"] == "free"
        # 무료 묶음을 화면에서 바꾸면 무료를 쓰는 미장도 다시 만든다
        body = {"base_url": "https://free2.test/v1", "model": "free-2", "api": "chat"}
        assert c.put("/api/llm/test-endpoint", json=body).status_code == 200
        assert us.runner.llm.model == "free-2" and kr.runner.llm.model == "paid-model"
        # 미장만 유료로
        assert c.put("/api/us/llm/profile", json={"active": "paid"}).json()["active"] == "paid"
        assert us.runner.llm.model == "paid-model"
        assert {m["code"]: m["llm_profile"] for m in c.get("/api/markets").json()} == {
            "KR": "paid",
            "US": "paid",
        }
        assert c.put("/api/llm/profile", json={"active": "free"}).status_code == 200  # 국장만 바뀐다
        assert kr.runner.llm.model == "free-2" and us.runner.llm.model == "paid-model"
        us.runner._run = {"active": True}
        assert c.put("/api/us/llm/profile", json={"active": "free"}).status_code == 409
        us.runner._run = {"active": False}


def test_us_disabled_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, us_market_enabled=False) as c:
        assert c.get("/api/us/portfolio").status_code == 404
        assert c.get("/api/us/llm/profile").status_code == 404
        ms = {m["code"]: m for m in c.get("/api/markets").json()}
        assert ms["US"]["enabled"] is False and ms["US"]["session"] is None
        assert [m["code"] for m in c.get("/api/overview").json()["markets"]] == ["KR"]
        assert "US" not in c.app.state.containers  # type: ignore[attr-defined]


def test_us_websocket_channel(tmp_path: Path) -> None:
    with (
        _client(tmp_path, kst(2026, 11, 5, 1, 0), auth_enabled=False) as c,
        c.websocket_connect("/ws/events?market=US") as ws,
    ):
        hello = ws.receive_json()
        assert hello["channel"] == "hello" and hello["data"]["market"] == "US"


def test_us_db_defaults(tmp_path: Path) -> None:
    s = Settings(_env_file=None, data_dir=tmp_path)  # type: ignore[call-arg]
    assert s.us_db_url() == f"sqlite:///{tmp_path / 'us_market.db'}"
    assert Settings(_env_file=None, database_url="sqlite://").us_db_url() == "sqlite://"  # type: ignore[call-arg]
    assert Settings(_env_file=None, us_database_url="sqlite:///x.db").us_db_url() == "sqlite:///x.db"  # type: ignore[call-arg]
    assert Settings(_env_file=None, agent_interval_minutes=30).us_interval_minutes == 30  # type: ignore[call-arg]
    assert Settings(_env_file=None, us_agent_interval_minutes="").us_agent_interval_minutes is None  # type: ignore[call-arg]


def test_us_file_db_migrates(tmp_path: Path) -> None:
    """파일 DB 면 미장 DB 에도 같은 Alembic 마이그레이션을 돌린다."""
    from app.main import create_app
    from tests.fake_toss import FakeTossClient

    clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'kr.db'}",
        data_dir=tmp_path,
        enable_scheduler=False,
    )
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        assert c.get("/api/us/portfolio").json()["currency"] == "USD"
    assert (tmp_path / "us_market.db").exists()


# ====================================================================== 토스 요청 형식 (미장)
@respx.mock
async def test_toss_us_request_params() -> None:
    from app.core.http import SafeHttpClient
    from app.market import toss as toss_mod
    from app.market.toss import TossClient, TossMarketProvider

    respx.post(f"{BASE}/oauth2/token").mock(
        return_value=httpx.Response(200, json={"access_token": "eyJt.p.s", "expires_in": 86400})
    )
    cal = respx.get(f"{BASE}/api/v1/market-calendar/US").mock(
        return_value=httpx.Response(200, json={"result": {"today": {"date": "2026-11-04"}}})
    )
    alls = respx.get(f"{BASE}/api/v1/stocks/all").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"symbol": "AAPL", "name": "애플", "securityType": "STOCK", "isCommonShare": True},
                    {"symbol": "QQQ", "name": "인베스코 QQQ", "securityType": "ETF", "isCommonShare": True},
                    {
                        "symbol": "XWAR",
                        "name": "워런트",
                        "securityType": "STOCK_WARRANTS",
                        "isCommonShare": True,
                    },
                ]
            },
        )
    )
    rk = respx.get(f"{BASE}/api/v1/rankings").mock(
        return_value=httpx.Response(200, json={"result": {"rankedAt": None, "rankings": []}})
    )
    tc = TossClient(
        "id",
        "secret",
        http=SafeHttpClient(toss_mod.ROUTES, rate_per_sec=1000, backoff_scale=0.0),
        rate_scale=100,
    )
    try:
        assert (await tc.market_calendar_us(date(2026, 11, 4)))["today"]["date"] == "2026-11-04"
        assert cal.calls.last.request.url.params["date"] == "2026-11-04"
        clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
        prov = TossMarketProvider(
            tc,
            clock,
            country="US",
            markets=("NASDAQ",),
            security_types=("STOCK", "ETF"),
            tz=UsMarketCalendar.tz,
        )
        uni = await prov.universe()
        assert set(uni) == {"AAPL", "QQQ"}  # 주식·ETF 만
        assert "securityType" not in alls.calls.last.request.url.params
        assert alls.calls.last.request.url.params["market"] == "NASDAQ"
        assert await prov.screen("change_1d", "ALL", 10) == []
        params = rk.calls.last.request.url.params
        assert (
            params["marketCountry"] == "US" and params["type"] == "TOP_GAINERS" and params["duration"] == "1d"
        )
    finally:
        await tc.aclose()


def test_us_stack_is_paper_even_when_live(tmp_path: Path) -> None:
    from app.container import build_container
    from app.core.config import LIVE_CONFIRM_PHRASE
    from tests.fake_toss import FakeTossClient

    clock = SimulatedClock(kst(2026, 11, 5, 1, 0))
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        data_dir=tmp_path,
        enable_scheduler=False,
        trading_mode="live",
        live_trading_confirm=LIVE_CONFIRM_PHRASE,
    )
    us = build_container(s, market="US", toss_client=FakeTossClient(clock), clock=clock)
    assert isinstance(us.broker, PaperBroker) and us.broker.is_paper and not us.broker.is_live
    assert us.trading is None and us.runner.live is False
