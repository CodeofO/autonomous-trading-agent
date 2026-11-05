"""장외 주문 (D-57): 토스 장 운영 캘린더의 장외 세션 인식, 켜고 끄기, 모의 체결 규칙, API. 네트워크 없음."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.brokers.base import OrderRequest, OrderSide, OrderStatus, OrderType
from app.brokers.paper import PaperBroker
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.core.market_calendar import MarketCalendar
from app.core.us_calendar import UsMarketCalendar
from app.db.session import Database
from app.engine import extended_hours as ext
from app.engine.settings_store import SettingsStore
from app.market.store import PriceStore
from app.risk.rules import OrderIntent
from tests.fake_toss import FakeTossClient
from tests.helpers import StaticProvider, make_container


def kst(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def t(day: str, hm: str) -> str:
    return f"{day}T{hm}:00+09:00"


def kr_entry(day: str) -> dict[str, Any]:
    """토스 `GET /api/v1/market-calendar/KR` 영업일 한 건 (명세 예시와 같은 모양)."""
    return {
        "date": day,
        "integrated": {
            "preMarket": {
                "startTime": t(day, "08:00"),
                "singlePriceAuctionStartTime": t(day, "08:50"),
                "endTime": t(day, "09:00"),
            },
            "regularMarket": {
                "startTime": t(day, "09:00"),
                "singlePriceAuctionStartTime": t(day, "15:20"),
                "endTime": t(day, "15:30"),
            },
            "afterMarket": {
                "startTime": t(day, "15:30"),
                "singlePriceAuctionEndTime": t(day, "15:40"),
                "endTime": t(day, "20:00"),
            },
        },
    }


def kr_calendar(*enabled: str) -> MarketCalendar:
    cal = MarketCalendar()
    cal.apply_toss_calendar(
        {
            "previousBusinessDay": kr_entry("2026-11-03"),
            "today": kr_entry("2026-11-04"),
            "nextBusinessDay": kr_entry("2026-11-05"),
        }
    )
    cal.extended_enabled = frozenset(enabled)
    return cal


def us_entry(day: str, next_day: str, *, edt: bool) -> dict[str, Any]:
    """토스 `GET /api/v1/market-calendar/US` 영업일 한 건 — 시각은 한국 시각, 서머타임이면 한 시간 이르다."""
    pre, open_, close, after = (
        ("17:00", "22:30", "05:00", "07:00") if edt else ("18:00", "23:30", "06:00", "08:00")
    )
    return {
        "date": day,
        "dayMarket": {"startTime": t(day, "09:00"), "endTime": t(day, "16:50")},
        "preMarket": {"startTime": t(day, pre), "endTime": t(day, open_)},
        "regularMarket": {"startTime": t(day, open_), "endTime": t(next_day, close)},
        "afterMarket": {"startTime": t(next_day, close), "endTime": t(next_day, after)},
    }


# ====================================================================== 캘린더
def test_kr_extended_sessions_follow_toss_calendar() -> None:
    off = kr_calendar()
    names = [(x.name, x.start, x.end) for x in off.extended_sessions() if x.day == date(2026, 11, 4)]
    assert names == [
        ("pre_market", kst(2026, 11, 4, 8, 0), kst(2026, 11, 4, 8, 50)),  # 접속매매 구간만 (시가단일가 전)
        ("after_market", kst(2026, 11, 4, 15, 40), kst(2026, 11, 4, 20, 0)),  # 단일가 뒤 접속매매부터
    ]
    assert off.fill_basis(kst(2026, 11, 4, 8, 10)) is None  # 끄면 장외 접수 없음
    assert off.fill_basis(kst(2026, 11, 4, 16, 0)) is None

    on = kr_calendar("pre_market", "after_market")
    assert on.fill_basis(kst(2026, 11, 4, 8, 10)) == "EXT"
    assert on.fill_basis(kst(2026, 11, 4, 8, 40)) == "EXT"  # 장전 접수 구간과 겹치면 프리마켓 시세로
    assert on.fill_basis(kst(2026, 11, 4, 8, 55)) == "OPEN"  # 시가단일가 — 기존 장전 접수
    assert on.fill_basis(kst(2026, 11, 4, 10, 0)) == "CLOSE"
    assert on.fill_basis(kst(2026, 11, 4, 15, 25)) is None  # 종가단일가
    assert on.fill_basis(kst(2026, 11, 4, 19, 59)) == "EXT"
    assert on.fill_basis(kst(2026, 11, 4, 20, 0)) is None
    assert "장외 프리마켓·애프터마켓" in on.order_hours_text()


def test_kr_holiday_has_no_extended_sessions() -> None:
    cal = MarketCalendar()
    cal.apply_toss_calendar({"today": {"date": "2026-11-04", "integrated": None}})
    cal.extended_enabled = frozenset({"pre_market", "after_market"})
    assert cal.extended_sessions() == [] and cal.fill_basis(kst(2026, 11, 4, 8, 10)) is None


def test_no_toss_calendar_means_no_extended_orders() -> None:
    cal = MarketCalendar()  # 토스 캘린더를 못 받음 → 장외 시각을 추측하지 않는다
    cal.extended_enabled = frozenset({"pre_market", "after_market"})
    assert cal.fill_basis(kst(2026, 11, 4, 8, 10)) is None
    assert cal.fill_basis(kst(2026, 11, 4, 10, 0)) == "CLOSE"


def test_us_extended_sessions_use_toss_times_across_dst() -> None:
    cal = UsMarketCalendar()
    cal.apply_toss_calendar(
        {
            "previousBusinessDay": us_entry("2026-10-30", "2026-10-31", edt=True),  # 서머타임
            "today": us_entry("2026-11-02", "2026-11-03", edt=False),  # 11/1 서머타임 끝
        }
    )
    sessions = {(x.day.isoformat(), x.name): x for x in cal.extended_sessions()}
    assert sessions[("2026-10-30", "pre_market")].start == kst(2026, 10, 30, 17, 0)
    assert sessions[("2026-11-02", "pre_market")].start == kst(2026, 11, 2, 18, 0)  # 한 시간 늦어짐
    assert sessions[("2026-11-02", "after_market")].end == kst(2026, 11, 3, 8, 0)
    s = cal.session(date(2026, 11, 2))
    assert s is not None and s.open == kst(2026, 11, 2, 23, 30)
    assert cal.fill_basis(kst(2026, 11, 2, 10, 0)) is None  # 데이마켓 꺼짐
    cal.extended_enabled = frozenset({"day_market"})
    assert cal.fill_basis(kst(2026, 11, 2, 10, 0)) == "EXT"
    assert cal.fill_basis(kst(2026, 11, 2, 19, 0)) is None  # 프리마켓은 꺼져 있음
    assert cal.fill_basis(kst(2026, 11, 2, 23, 10)) == "OPEN"  # 기존 장전 접수(모의)는 그대로


# ====================================================================== 모의 체결
class TimedQuotes:
    """현재가 + 시세 시각(토스 `timestamp`)."""

    def __init__(self, prices: dict[str, tuple[float, datetime | None]]) -> None:
        self.prices = prices

    async def last_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.prices[s][0] for s in symbols if s in self.prices}

    async def last_quotes(self, symbols: list[str]) -> dict[str, tuple[float, datetime | None]]:
        return {s: self.prices[s] for s in symbols if s in self.prices}


def kr_broker(now: datetime, quotes: TimedQuotes, *enabled: str) -> tuple[PaperBroker, SimulatedClock]:
    db = Database("sqlite://")
    db.create_all()
    clock = SimulatedClock(now)
    broker = PaperBroker(
        db,
        PriceStore(db, StaticProvider()),
        kr_calendar(*enabled),
        clock,
        initial_cash=10_000_000,
        quotes=quotes,
    )
    return broker, clock


async def test_paper_after_market_fills_only_on_fresh_quote_then_expires() -> None:
    q = TimedQuotes(
        {"005930": (71_000.0, kst(2026, 11, 4, 15, 55)), "000660": (180_000.0, kst(2026, 11, 4, 15, 30))}
    )
    broker, clock = kr_broker(kst(2026, 11, 4, 16, 0), q, "after_market")
    fresh = await broker.place_order(OrderRequest("c1", "005930", OrderSide.BUY, 3))
    assert fresh.status == OrderStatus.FILLED and fresh.fill_basis == "EXT"
    assert fresh.trade_day == date(2026, 11, 4) and fresh.fills[0].price > 71_000  # 슬리피지
    # 마지막 체결이 정규장 시각(15:30) → 장외 거래가 없는 종목은 체결하지 않고 기다린다
    stale = await broker.place_order(OrderRequest("c2", "000660", OrderSide.BUY, 1))
    assert stale.status == OrderStatus.SUBMITTED
    clock.set(kst(2026, 11, 4, 19, 0))
    assert await broker.settle() == []
    q.prices["000660"] = (179_500.0, kst(2026, 11, 4, 19, 0))  # 장외 체결이 생김
    done = await broker.settle()
    assert [r.status for r in done] == [OrderStatus.FILLED]


async def test_paper_extended_order_expires_at_session_end() -> None:
    q = TimedQuotes({"000660": (180_000.0, kst(2026, 11, 4, 15, 30))})
    broker, clock = kr_broker(kst(2026, 11, 4, 16, 0), q, "after_market")
    res = await broker.place_order(OrderRequest("c1", "000660", OrderSide.BUY, 1, OrderType.LIMIT, 150_000))
    assert res.status == OrderStatus.SUBMITTED
    clock.set(kst(2026, 11, 4, 20, 0))
    out = await broker.settle()
    assert [(r.status, r.reject_reason) for r in out] == [(OrderStatus.CANCELED, "ext-session-expired")]
    assert broker._reserved() == 0


async def test_paper_extended_rejected_when_session_off() -> None:
    q = TimedQuotes({"005930": (71_000.0, kst(2026, 11, 4, 16, 0))})
    broker, _ = kr_broker(kst(2026, 11, 4, 16, 0), q)
    res = await broker.place_order(OrderRequest("c1", "005930", OrderSide.BUY, 1))
    assert res.status == OrderStatus.REJECTED and res.reject_reason == "order-hours-closed"


# ====================================================================== 설정 저장·엔진
def test_settings_store_roundtrip_and_validation(tmp_path: Path) -> None:
    db = Database("sqlite://")
    db.create_all()
    kv = SettingsStore(db)
    cal = MarketCalendar()
    assert ext.apply(kv, cal) == {"pre_market": False, "after_market": False}
    assert ext.save(kv, cal, {"after_market": True}) == {"pre_market": False, "after_market": True}
    assert cal.extended_enabled == frozenset({"after_market"})
    try:
        ext.save(kv, cal, {"day_market": True})  # 국장에는 데이마켓이 없다
    except ValueError as e:
        assert "day_market" in str(e)
    else:
        raise AssertionError("국장 day_market 은 거절해야 한다")


def test_engine_order_window_and_risk_in_extended_session(tmp_path: Path) -> None:
    c = make_container(tmp_path, kst(2026, 11, 4, 16, 0))
    c.cal.apply_toss_calendar({"today": kr_entry("2026-11-04")})
    blocked = c.engine.orders.risk.check(OrderIntent("005930", "BUY", 1, 70_000, "장외"), _ctx(c))
    assert not blocked.allowed and "장 운영시간 외" in blocked.reasons[0]
    ext.save(c.kv, c.cal, {"after_market": True})
    assert "장외 애프터마켓" in c.engine.order_window() and "20:00 KST" in c.engine.order_window()
    assert c.engine.orders.risk.check(OrderIntent("005930", "BUY", 1, 70_000, "장외"), _ctx(c)).allowed


def _ctx(c: Any) -> Any:
    import asyncio

    return asyncio.run(c.engine.risk_context())


# ====================================================================== API
def test_extended_hours_api_per_market(tmp_path: Path) -> None:
    from app.main import create_app

    clock = SimulatedClock(kst(2026, 11, 4, 16, 0))
    s = Settings(database_url="sqlite://", data_dir=tmp_path, enable_scheduler=False, auth_enabled=False)
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        kr = c.get("/api/extended-hours").json()
        assert [x["key"] for x in kr["sessions"]] == ["pre_market", "after_market"]
        assert not any(x["enabled"] for x in kr["sessions"]) and kr["live"] is False
        on = c.put("/api/extended-hours", json={"after_market": True}).json()
        assert {x["key"]: x["enabled"] for x in on["sessions"]} == {"pre_market": False, "after_market": True}
        assert c.put("/api/extended-hours", json={"day_market": True}).status_code == 400
        assert "장외 주문 설정을 바꿨어요" in c.get("/api/chat").text
        us = c.get("/api/us/extended-hours").json()
        assert [x["key"] for x in us["sessions"]] == ["day_market", "pre_market", "after_market"]
        assert not any(x["enabled"] for x in us["sessions"])  # 국장 설정과 따로
        assert c.put("/api/us/extended-hours", json={"day_market": True}).status_code == 200
        assert c.get("/api/us/extended-hours").json()["sessions"][0]["enabled"] is True


# ====================================================================== 장외 세션 판단 일정 (D-61)
def _stub(calls: list[str], name: str) -> Any:
    async def run() -> None:
        calls.append(name)

    return run


async def test_kr_after_market_adds_decisions_until_ten_minutes_before_end(tmp_path: Path) -> None:
    from app.main import create_app

    clock = SimulatedClock(kst(2026, 11, 4, 8, 20))
    s = Settings(database_url="sqlite://", data_dir=tmp_path, enable_scheduler=False, auth_enabled=False)
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        kr.cal.apply_toss_calendar(
            {"today": kr_entry("2026-11-04"), "nextBusinessDay": kr_entry("2026-11-05")}
        )
        c.put("/api/schedule", json={"start": "08:00", "end": "16:00", "interval_minutes": 60})
        off = c.get("/api/schedule").json()
        assert off["extended"] == [] and off["total"] == 9
        c.put("/api/extended-hours", json={"after_market": True})
        sc = c.get("/api/schedule").json()
        assert sc["times"] == [f"{h:02d}:00" for h in range(8, 17)]  # 일정 범위는 그대로
        assert sc["extended"] == [
            {
                "key": "after_market",
                "label": "애프터마켓",
                "start": "15:40",
                "end": "20:00",
                "start_kst": "15:40",
                "end_kst": "20:00",
                "times": ["17:00", "18:00", "19:00"],  # 16:00 은 일정 범위와 겹쳐 한 번만, 19:50 까지
                "times_kst": ["17:00", "18:00", "19:00"],
            }
        ]
        assert sc["total"] == 12 and sc["outside_market"] == ["08:00"]  # 프리마켓은 꺼져 있음
        assert "장외 애프터마켓 포함" in kr.scheduler.describe()

        calls: list[str] = []
        kr.engine.hourly = _stub(calls, "hourly")
        kr.scheduler._calendar_day = kr.cal.local_date(kst(2026, 11, 4, 18))  # 가짜 토스로 캘린더를 덮지 않게
        clock.set(kst(2026, 11, 4, 18, 0))
        assert await kr.scheduler.tick(wait=True) == ["2026-11-04 18:00 hourly"]
        assert calls == ["hourly"]


async def test_us_day_market_decisions_cross_new_york_midnight(tmp_path: Path) -> None:
    from app.main import create_app

    clock = SimulatedClock(kst(2026, 11, 4, 8, 0))  # 뉴욕 11/3 18:00 (표준시)
    s = Settings(database_url="sqlite://", data_dir=tmp_path, enable_scheduler=False, auth_enabled=False)
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        us.cal.apply_toss_calendar(
            {
                "today": us_entry("2026-11-04", "2026-11-05", edt=False),
                "nextBusinessDay": us_entry("2026-11-05", "2026-11-06", edt=False),
            }
        )
        c.put("/api/us/schedule", json={"start": "09:00", "end": "16:00", "interval_minutes": 60})
        on = {"day_market": True, "pre_market": True, "after_market": True}
        assert c.put("/api/us/extended-hours", json=on).status_code == 200
        sc = c.get("/api/us/schedule").json()
        assert sc["day"] == "2026-11-04"
        by = {x["key"]: x for x in sc["extended"]}
        # 데이마켓: 한국 09:00~16:50 = 뉴욕 전날 19:00~02:50 → 자정을 넘어 02:00 까지
        assert by["day_market"]["times"] == [
            "19:00",
            "20:00",
            "21:00",
            "22:00",
            "23:00",
            "00:00",
            "01:00",
            "02:00",
        ]
        assert by["day_market"]["times_kst"][0] == "09:00" and by["day_market"]["times_kst"][-1] == "16:00"
        assert by["pre_market"]["times"] == [
            "04:00",
            "05:00",
            "06:00",
            "07:00",
            "08:00",
        ]  # 09:00 은 일정 범위에
        assert by["after_market"]["times"] == ["17:00"]  # 16:00~18:00, 끝 10분 전까지
        assert sc["total"] == 8 + 8 + 5 + 1 and sc["outside_market"] == []
        assert sc["in_extended"] == ["09:00", "16:00"]

        calls: list[str] = []
        us.engine.hourly = _stub(calls, "hourly")
        us.engine.morning = _stub(calls, "morning")
        now = kst(2026, 11, 4, 10, 0)  # 데이마켓 = 뉴욕 11/3 20:00 — 11/4 거래일 일정으로 돈다
        us.scheduler._calendar_day = us.cal.local_date(now)
        clock.set(now)
        assert await us.scheduler.tick(wait=True) == ["2026-11-04 10:00 hourly"]
        assert us.scheduler.next_runs(1)[0] == {"name": "hourly", "at": "2026-11-04T11:00:00+09:00"}
        # 데이마켓을 끄면 그 시각은 빠진다
        c.put("/api/us/extended-hours", json={"day_market": False})
        assert c.get("/api/us/schedule").json()["total"] == 8 + 5 + 1
        assert us.scheduler.next_runs(1)[0]["at"] == "2026-11-04T18:00:00+09:00"  # 프리마켓 04:00 (뉴욕)
