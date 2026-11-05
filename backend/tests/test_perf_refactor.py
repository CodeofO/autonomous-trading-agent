"""성능 리팩토링(docs/perf/REFACTOR_PLAN.md B1~B10)이 바꾼 계약을 고정한다.

- 요청당 SQL 수가 기록 건수와 관계없이 일정하다 (/orders·/fills·/portfolio·/system·/agent/structure·/watchlist)
- 캐시(종목 이름·kv·매매 원장·LLM 비용)는 쓰기 직후 바로 새 값이다
- 장외에는 1분 감시·10분 갱신이 토스·DB 를 부르지 않는다 (미장 세션이 한국 자정을 넘는 경우 포함)
- 자산 스냅샷: 장중 + 마감 뒤 하루 한 번
- gzip 응답 (작은 응답·WebSocket 제외)
- 하루 한 번 정리: 만료된 api_cache, 모의계좌 JSON 의 오래된 끝난 주문
- 마이그레이션 0004 up/down (SQLite) 과 모델 스키마 일치
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select

from app.agent.costs import ledger_view, record_call
from app.agent.llm import ChatResult, LLMClient
from app.brokers.base import OrderRequest, OrderSide, OrderStatus
from app.container import build_container
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.db.models import ApiCache, EquitySnapshot, Fill, Order, WatchlistItem
from app.db.session import Database
from app.engine.maintenance import prune_api_cache, run_maintenance
from app.engine.settings_store import SettingsStore
from app.engine.trades import trade_ledger
from app.main import create_app
from tests.fake_toss import STOCKS, FakeTossClient, at
from tests.helpers import bar, make_container, make_env

D = date(2026, 11, 2)  # 월요일 (국장 거래일, 미국 장은 한국 시각 11/2 23:30 ~ 11/3 06:00)


class SqlCount:
    """before_cursor_execute 로 실행된 SQL 문 수를 센다."""

    def __init__(self, *engines: Engine) -> None:
        self.n = 0
        self.statements: list[str] = []
        self.engines = engines

    def _hook(self, conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        self.n += 1
        self.statements.append(statement)

    @contextmanager
    def __call__(self) -> Iterator[SqlCount]:
        self.n = 0
        self.statements = []
        for e in self.engines:
            event.listen(e, "before_cursor_execute", self._hook)
        try:
            yield self
        finally:
            for e in self.engines:
                event.remove(e, "before_cursor_execute", self._hook)


def _app(tmp_path: Path, start: datetime | None = None, **kw: Any) -> TestClient:
    clock = SimulatedClock(start or at(D, 10))
    s = Settings(
        database_url="sqlite://", data_dir=tmp_path, auth_enabled=False, enable_scheduler=False, **kw
    )
    return TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock))


def _seed_history(c: Any, n: int) -> None:
    """종목 10개 일봉 + 주문·체결 n건 + 보유 3종목 + 관심종목 5개."""
    syms = [s.symbol for s in STOCKS[:10]]
    c.kv.set("account_source", {**c.kv.get("account_source"), "since": at(D, 9).isoformat()})
    c.store.upsert([bar(sym, D - timedelta(days=1), 100, 110, 90, 100, name=f"이름{sym}") for sym in syms])
    base = at(D, 9, 5)
    with c.db.session() as s:
        start = (s.scalar(select(func.max(Order.id))) or 0) + 1
        for i in range(start, start + n):
            sym = syms[i % len(syms)]
            t = base + timedelta(seconds=i)
            s.add(
                Order(
                    id=i,
                    client_order_id=f"seed-{i}",
                    symbol=sym,
                    side="BUY",
                    order_type="MARKET",
                    quantity=1,
                    status="FILLED",
                    filled_qty=1,
                    created_at=t,
                    updated_at=t,
                )
            )
            s.flush()
            s.add(Fill(order_id=i, symbol=sym, side="BUY", quantity=1, price=100.0, filled_at=t))
        for sym in syms[:5]:
            if s.get(WatchlistItem, sym) is None:
                s.add(WatchlistItem(symbol=sym, name=""))
    for sym in syms[:3]:
        c.broker.state["positions"][sym] = {"qty": 2, "avg_price": 100.0, "opened_day": "2026-10-01"}
    c.broker._save()


# ====================================================================== B1·B3: 요청당 SQL 수
ENDPOINT_SQL_MAX = {
    "/api/orders?limit=300": 2,
    "/api/fills?limit=200": 2,
    "/api/portfolio": 4,
    "/api/system": 2,
    "/api/agent/structure": 5,
    "/api/watchlist": 2,
    "/api/us/orders?limit=300": 2,
    "/api/us/system": 2,
}


def test_sql_per_request_is_flat(tmp_path: Path) -> None:
    """기록이 늘어도 요청당 SQL 수가 늘지 않는다 (예전: /orders 300건 = 302문)."""
    with _app(tmp_path) as client:
        app = client.app
        kr, us = app.state.containers["KR"], app.state.containers["US"]  # type: ignore[attr-defined]
        count = SqlCount(kr.db.engine, us.db.engine)
        seen: dict[str, list[int]] = {}
        for n in (20, 120):  # 기록을 늘려 가며 같은 요청의 SQL 수를 본다
            _seed_history(kr, n)
            _seed_history(us, n)
            for path, limit in ENDPOINT_SQL_MAX.items():
                assert client.get(path).status_code == 200  # 캐시 데우기
                with count() as q:
                    r = client.get(path)
                assert r.status_code == 200
                assert q.n <= limit, (path, q.n, q.statements)
                seen.setdefault(path, []).append(q.n)
        assert all(a == b for a, b in seen.values()), seen  # 기록 수와 무관
        assert len(client.get("/api/orders?limit=300").json()) == 140
        assert len(client.get("/api/us/fills?limit=200").json()) == 140
        assert len(client.get("/api/portfolio").json()["positions"]) == 3
        names = {o["symbol"]: o["name"] for o in client.get("/api/orders?limit=300").json()}
        assert names[STOCKS[0].symbol] == f"이름{STOCKS[0].symbol}"  # 이름은 그대로 붙는다


def test_overview_reads_markets_concurrently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/overview 는 국장·미장을 동시에 조회한다 (B9) — 응답 순서는 그대로 KR, US."""
    order: list[str] = []
    with _app(tmp_path) as client:
        for code, st in client.app.state.containers.items():  # type: ignore[attr-defined]
            real = st.engine.portfolio

            async def slow(real: Any = real, code: str = code) -> dict[str, Any]:
                order.append(f"start {code}")
                await asyncio.sleep(0.05)
                out = await real()
                order.append(f"end {code}")
                return out

            monkeypatch.setattr(st.engine, "portfolio", slow)
        r = client.get("/api/overview")
    assert r.status_code == 200
    assert [m["code"] for m in r.json()["markets"]] == ["KR", "US"]
    assert order[:2] == ["start KR", "start US"]  # 하나가 끝나기 전에 둘 다 시작


# ====================================================================== B1: 종목 이름 캐시
def test_name_cache_follows_upserts() -> None:
    env = make_env(bars=[bar("AAA", D - timedelta(days=2), 1, 1, 1, 1, name="옛이름")])
    count = SqlCount(env.db.engine)
    assert env.store.name_of("AAA") == "옛이름"
    with count() as q:
        assert env.store.name_of("AAA") == "옛이름"
        assert env.store.names(["AAA"]) == {"AAA": "옛이름"}
    assert q.n == 0  # 캐시
    env.store.upsert([bar("AAA", D - timedelta(days=1), 1, 1, 1, 1, name="새이름")])
    assert env.store.name_of("AAA") == "새이름"  # 일봉을 쓰면 바로 새 이름
    env.store.upsert([bar("AAA", D - timedelta(days=30), 1, 1, 1, 1, name="아주옛이름")])
    assert env.store.name_of("AAA") == "새이름"  # 이름 = 마지막 일봉 기준 (과거 일봉을 넣어도 그대로)
    assert env.store.name_of("BBB") == ""  # 일봉 없음 → 캐시하지 않는다
    env.store.upsert([bar("BBB", D - timedelta(days=1), 1, 1, 1, 1, name="비")])
    assert env.store.name_of("BBB") == "비"
    bars = env.store.last_bars(["AAA", "BBB", "CCC"])
    assert set(bars) == {"AAA", "BBB"}
    for sym, b in bars.items():
        assert b == env.store.last_bar(sym)


def test_paper_positions_read_bars_once() -> None:
    """보유 종목 평가는 종목 수와 관계없이 일봉 조회 1번 (현재가가 없을 때), 잔고·보유 종목은 같이 계산."""
    syms = [f"S{i:02d}" for i in range(6)]
    env = make_env(bars=[bar(s, D - timedelta(days=1), 100, 100, 100, 100 + i) for i, s in enumerate(syms)])
    for s in syms:
        env.broker.state["positions"][s] = {"qty": 1, "avg_price": 100.0, "opened_day": "2026-10-01"}
    count = SqlCount(env.db.engine)
    with count() as q:
        bal, pos = asyncio.run(env.broker.account())
    assert q.n == 1
    assert [p.symbol for p in pos] == [p.symbol for p in asyncio.run(env.broker.get_positions())]
    assert bal == asyncio.run(env.broker.get_balance())
    assert sorted(p.last_price for p in pos) == [100.0 + i for i in range(6)]


# ====================================================================== B3: kv 캐시
async def test_kv_cache_is_write_through(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    other = SettingsStore(c.db)  # 같은 DB 의 다른 저장소도 같은 캐시를 본다
    count = SqlCount(c.db.engine)
    c.kv.get("risk")  # 데우기
    with count() as q:
        for _ in range(5):
            assert c.engine.status == "RUNNING"
            c.kv.get("account_source")
            c.kv.get("없는키")
    assert q.n == 0
    await c.engine.kill("테스트")  # 킬 스위치는 쓰자마자 보인다
    assert c.engine.status == "KILLED" and other.get("engine")["status"] == "KILLED"
    assert (await c.engine.risk_context()).killed
    c.engine.resume()
    assert c.engine.status == "RUNNING"
    v = c.kv.get("engine")
    v["status"] = "망가뜨림"  # 돌려받은 값을 고쳐도 캐시는 그대로
    assert c.kv.get("engine")["status"] == "RUNNING"
    c.kv.set("tuple", {"p": (1, 2)})
    assert c.kv.get("tuple") == {"p": [1, 2]}  # DB 에 저장됐다 읽힌 모양 그대로
    with pytest.raises(Exception):  # noqa: B017 — JSON 으로 저장할 수 없는 값
        c.kv.set("bad", {"x": object()})
    assert c.kv.get("bad") == {}  # 저장 실패 → 캐시도 그대로
    other.invalidate()
    assert other.get("engine")["status"] == "RUNNING"  # DB 에서 다시 읽어도 같다


def test_kv_cache_is_per_market_db(tmp_path: Path) -> None:
    clock = SimulatedClock(at(D, 10))
    s = Settings(database_url="sqlite://", data_dir=tmp_path, enable_scheduler=False)
    kr = build_container(s, create_schema=True, toss_client=FakeTossClient(clock), clock=clock)
    us = build_container(s, create_schema=True, market="US", shared=kr, clock=clock)
    assert kr.db is not us.db
    kr.kv.set("risk", {**kr.kv.get("risk"), "max_order_amount": 1})
    assert us.kv.get("risk")["max_order_amount"] == 1500  # 미장 kv 는 따로
    us.engine._set_status("PAUSED")
    assert us.engine.status == "PAUSED" and kr.engine.status == "RUNNING"
    assert us.llm_kv is kr.kv  # LLM 설정은 국장 kv 하나 (예전과 같음)


# ====================================================================== B2: 매매 원장 캐시
def _add_fill(db: Database, oid: int, sym: str, side: str, qty: int, px: float, t: datetime) -> None:
    with db.session() as s:
        s.add(
            Order(
                id=oid,
                client_order_id=f"o{oid}",
                symbol=sym,
                side=side,
                order_type="MARKET",
                quantity=qty,
                status="FILLED",
                reason="이유",
                created_at=t,
                updated_at=t,
            )
        )
        s.flush()
        s.add(Fill(order_id=oid, symbol=sym, side=side, quantity=qty, price=px, fee=1.0, filled_at=t))


def test_trade_ledger_cache_tracks_fills() -> None:
    db = Database("sqlite://")
    db.create_all()
    t0 = datetime(2026, 11, 2, 1, tzinfo=UTC)
    _add_fill(db, 1, "A", "BUY", 10, 100.0, t0)
    _add_fill(db, 2, "A", "SELL", 4, 120.0, t0 + timedelta(minutes=1))
    names = {"A": "에이"}.get
    first = trade_ledger(db, lambda s: names(s) or "")
    assert first["fill_count"] == 2 and first["realized_pnl_by_stock"] == {
        "에이": round((120 - 100.1) * 4 - 1)
    }
    count = SqlCount(db.engine)
    with count() as q:
        assert trade_ledger(db, lambda s: names(s) or "") == first
    assert q.n == 1  # (건수, 마지막 id) 확인만
    _add_fill(db, 3, "A", "SELL", 6, 90.0, t0 + timedelta(minutes=2))  # 뒤에 붙은 체결 → 이어서 계산
    after = trade_ledger(db, lambda s: names(s) or "", limit=2)
    assert after["fill_count"] == 3 and [f["price"] for f in after["recent_fills"]] == [90, 120]
    _add_fill(db, 4, "B", "BUY", 1, 50.0, t0 - timedelta(days=1))  # 앞선 시각의 체결 → 처음부터 다시
    assert trade_ledger(db)["recent_fills"][-1]["symbol"] == "B"
    with db.session() as s:
        s.delete(s.get(Fill, 2))
    assert trade_ledger(db)["fill_count"] == 3  # 지워진 체결도 반영
    since = t0 + timedelta(seconds=30)
    assert trade_ledger(db, since=since)["fill_count"] == 1  # 기준 시각별로 따로


# ====================================================================== B5: LLM 비용 캐시
def test_costs_summary_cache_invalidates_on_new_usage() -> None:
    db = Database("sqlite://")
    db.create_all()
    now = datetime(2026, 11, 4, 10, tzinfo=KST)
    llm = LLMClient("http://llm.local.test/v1", "m", "", profile="free")

    def call(ts: datetime, kind: str, run_id: int | None, inp: int, out: int) -> None:
        res = ChatResult(content="", usage={"prompt_tokens": inp, "completion_tokens": out})
        record_call(db, ts, kind=kind, run_id=run_id, llm=llm, res=res, market="KR")

    call(now - timedelta(hours=1), "session", 1, 1000, 10)
    first = ledger_view(db, now, 2)
    count = SqlCount(db.engine)
    with count() as q:
        again = ledger_view(db, now, 2)
    assert again == first and q.n == 1
    again["groups"].clear()  # 돌려받은 값을 고쳐도 캐시는 그대로
    assert ledger_view(db, now, 2) == first
    call(now, "chat", None, 500, 5)
    fresh = ledger_view(db, now, 2)
    assert sum(g["input"] for g in fresh["groups"]) == 1500
    assert {g["kind"] for g in fresh["groups"]} == {"session", "chat"}
    assert ledger_view(db, now, 2, "2026-10") != fresh  # 고른 달이 바뀌면 다시 계산


# ====================================================================== B6: 장 시간 밖 작업
async def test_intraday_does_nothing_outside_session_window(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 20))
    fake = c.toss.client
    count = SqlCount(c.db.engine)
    for t in (
        at(D, 20),
        at(D, 8, 40),
        at(D, 15, 36),
        at(date(2026, 11, 7), 10),
    ):  # 장후·장전·마감 6분 뒤·토요일
        c.clock.set(t)
        c.engine._health = (True, -1e9)  # 연결 확인 캐시 만료 상태
        n0 = len(fake.calls)
        with count() as q:
            await c.engine.intraday()
        assert len(fake.calls) == n0 and q.n == 0, t
    for t in (at(D, 8, 59), at(D, 10, 7), at(D, 15, 35)):  # 장 시작 1분 전 ~ 마감 5분 뒤는 예전처럼
        c.clock.set(t)
        c.engine._health = (True, -1e9)
        n0 = len(fake.calls)
        await c.engine.intraday()
        assert "prices" in fake.calls[n0:], t


async def test_kr_refresh_runs_only_near_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = make_container(tmp_path, at(D, 10))
    ran: list[datetime] = []

    async def sync() -> dict[str, Any]:
        ran.append(c.clock.now())
        return {}

    monkeypatch.setattr(c.engine, "sync_data", sync)
    plan = {
        at(D, 7, 50): False,
        at(D, 8, 0): True,  # 장 시작 1시간 전부터
        at(D, 12, 0): True,
        at(D, 16, 0): True,  # 마감 30분 뒤까지
        at(D, 16, 10): False,
        at(D, 23, 0): False,
        at(date(2026, 11, 7), 10): False,  # 토요일
    }
    for t, expect in plan.items():
        c.clock.set(t)
        ran.clear()
        await c.scheduler.refresh()
        assert bool(ran) == expect, t


async def test_us_jobs_follow_session_across_kst_midnight(tmp_path: Path) -> None:
    clock = SimulatedClock(at(D, 12))
    s = Settings(database_url="sqlite://", data_dir=tmp_path, enable_scheduler=False)
    kr = build_container(s, create_schema=True, toss_client=FakeTossClient(clock), clock=clock)
    us = build_container(s, create_schema=True, market="US", shared=kr, clock=clock)
    fake = us.toss.client
    nxt = D + timedelta(days=1)
    cases = {
        at(D, 12): False,  # 한국 낮 = 미국 장외
        at(D, 23, 20): False,  # 장 시작(23:30 KST) 10분 전
        at(D, 23, 29): True,  # 장 시작 1분 전
        at(nxt, 0, 30): True,  # 한국 자정 넘어 (뉴욕 11/2 10:30)
        at(nxt, 6, 5): True,  # 마감(06:00 KST) 5분 뒤
        at(nxt, 6, 6): False,
    }
    for t, expect in cases.items():
        clock.set(t)
        us.engine._health = (True, -1e9)
        n0 = len(fake.calls)
        await us.engine.intraday()
        assert ("prices" in fake.calls[n0:]) == expect, t
    near = {at(D, 22, 29): False, at(D, 22, 30): True, at(nxt, 6, 30): True, at(nxt, 6, 31): False}
    for t, expect in near.items():
        assert us.scheduler._near_session(t) == expect, t


async def test_equity_snapshots_in_session_and_once_after_close(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 7))

    def n() -> int:
        with c.db.session() as s:
            return int(s.scalar(select(func.count()).select_from(EquitySnapshot)) or 0)

    steps = [
        (at(D, 7), "sync", 0),  # 장 시작 전: 기록 안 함 (예전: 10분마다)
        (at(D, 8, 35), "sync", 0),
        (at(D, 10), "sync", 1),  # 장중: 기록
        (at(D, 10, 5), "sync", 0),  # 최근 10분 안에 기록이 있음 (예전: 갱신마다 기록)
        (at(D, 10, 10), "intraday", 1),  # 장중 10분마다 (1분 감시)
        (at(D, 10, 11), "intraday", 0),
        (at(D, 10, 21), "sync", 1),  # 마지막 기록(10:10)에서 10분이 넘음
        (at(D, 15, 30), "intraday", 1),  # 마감 시각
        (at(D, 15, 31), "intraday", 0),  # 마감 뒤 기록이 이미 있음
        (at(D, 16), "sync", 0),
        (at(D, 22), "sync", 0),
        (at(D + timedelta(days=1), 7), "sync", 0),
        (at(date(2026, 11, 7), 12), "sync", 0),  # 토요일
    ]
    for t, job, added in steps:
        c.clock.set(t)
        before = n()
        await (c.engine.sync_data() if job == "sync" else c.engine.intraday())
        assert n() - before == added, (t, job)
    # 마감 시각에 서버가 꺼져 있었으면 마감 뒤 첫 갱신 때 한 번
    d2 = D + timedelta(days=1)
    c.clock.set(at(d2, 17))
    before = n()
    await c.engine.sync_data()
    await c.engine.sync_data()
    assert n() - before == 1


# ====================================================================== B4: gzip
def test_gzip_for_large_responses_only(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        kr = client.app.state.containers["KR"]  # type: ignore[attr-defined]
        for i in range(60):
            kr.hub.chat("system", "text", f"긴 메시지 {i} " + "가" * 50)
        r = client.get("/api/chat?limit=200", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
        assert r.num_bytes_downloaded < len(r.content) / 3 and len(r.json()) >= 60
        plain = client.get("/api/chat?limit=200", headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in plain.headers and plain.json() == r.json()
        small = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in small.headers  # 1KB 미만은 그대로
        with client.websocket_connect("/ws/events") as ws:  # WebSocket 은 영향 없음
            assert ws.receive_json()["channel"] == "hello"
            ws.send_text("PING")
            assert ws.receive_json()["channel"] == "pong"


# ====================================================================== B7: 정리 작업
def test_api_cache_prune_respects_ttl() -> None:
    db = Database("sqlite://")
    db.create_all()
    now = datetime(2026, 11, 5, 4, 40, tzinfo=KST)
    old, fresh = now - timedelta(days=2), now - timedelta(hours=3)
    rows = {
        "dart:fs:00126380:2026:11014:CFS": ({"rows": []}, old),  # 빈 결과 + 1일 지남 → 삭제
        "dart:fs:00126380:2026:11014:OFS": ({"rows": []}, fresh),  # 빈 결과 + 1일 안 → 유지
        "dart:fs:00126380:2025:11011:CFS": ({"rows": [{"account_nm": "매출액", "x": "1" * 50}]}, old),  # 유지
        "dart:fs:00126380:2022:11011:CFS": (
            {"rows": [{"account_nm": "매출액"}]},
            old,
        ),  # 다시 안 읽는 연도 → 삭제
        "dart:fs:00126380:2023:11011:CFS": ({"rows": [{"account_nm": "매출액"}]}, old),  # 여유 1년 → 유지
        "other:key": ({"rows": []}, old),  # 다른 종류의 캐시는 건드리지 않는다
    }
    with db.session() as s:
        for k, (payload, t) in rows.items():
            s.add(ApiCache(key=k, payload=payload, fetched_at=t))
    assert prune_api_cache(db, now) == 2
    with db.session() as s:
        left = set(s.scalars(select(ApiCache.key)))
    assert left == set(rows) - {"dart:fs:00126380:2026:11014:CFS", "dart:fs:00126380:2022:11011:CFS"}


async def test_paper_json_keeps_open_and_recent_closed_orders() -> None:
    env = make_env(start=at(D, 8, 40))  # 장전 → 주문은 시가 체결 대기(미체결)
    env.store.upsert([bar("005930", D - timedelta(days=1), 100, 100, 100, 100)])
    env.broker.keep_closed_orders = 3
    opened = await env.broker.place_order(OrderRequest("open-1", "005930", OrderSide.BUY, 1))
    assert opened.status == OrderStatus.SUBMITTED
    for i in range(6):  # 수량 0 → 거부(끝난 주문)
        r = await env.broker.place_order(OrderRequest(f"bad-{i}", "005930", OrderSide.BUY, 0))
        assert r.status == OrderStatus.REJECTED and r.reject_reason == "invalid-quantity"
    orders = env.broker.state["orders"]
    assert opened.broker_order_id in orders  # 미체결은 남는다
    assert [o["client_order_id"] for o in orders.values() if o["status"] == "REJECTED"] == [
        "bad-3",
        "bad-4",
        "bad-5",
    ]
    assert set(env.broker.state["client_ids"]) == {"open-1", "bad-3", "bad-4", "bad-5"}
    # 오래된 주문을 취소해도(끝난 주문 중 가장 오래됨) 결과는 그대로 돌려준다
    res = await env.broker.cancel_order(opened.broker_order_id)
    assert res.status == OrderStatus.CANCELED and res.client_order_id == "open-1"
    assert opened.broker_order_id not in env.broker.state["orders"]
    assert len(env.broker.state["orders"]) == 3
    seq = env.broker.state["seq"]
    again = await env.broker.place_order(OrderRequest("bad-5", "005930", OrderSide.BUY, 0))
    assert env.broker.state["seq"] == seq and again.reject_reason == "invalid-quantity"  # 멱등 유지
    reloaded = type(env.broker)(env.db, env.store, env.cal, env.clock, initial_cash=1)
    assert reloaded.state["orders"] == env.broker.state["orders"]  # 저장된 JSON 도 정리됨
    # 정리하지 않는 브로커(백테스트·기본값)는 모두 남긴다
    assert (
        type(env.broker)(
            Database("sqlite://"), env.store, env.cal, env.clock, initial_cash=1, persist=False
        ).keep_closed_orders
        is None
    )


def test_maintenance_runs_on_startup_and_never_breaks_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.engine.maintenance as maint

    called: list[str] = []

    def boom(db: Database, now: datetime) -> int:
        called.append("api_cache")
        raise RuntimeError("정리 실패")

    monkeypatch.setattr(maint, "prune_api_cache", boom)
    with _app(tmp_path) as client:
        assert client.get("/api/health").status_code == 200
    assert called == ["api_cache", "api_cache"]  # 국장·미장 기동 때 한 번씩, 실패해도 기동은 계속
    db = Database("sqlite://")
    db.create_all()
    assert run_maintenance(db, datetime.now(UTC)) == {"api_cache": 0, "paper_orders": 0}


async def test_scheduler_registers_daily_maintenance(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.scheduler.start()
    try:
        job = c.scheduler._sched.get_job("maintenance")  # type: ignore[union-attr]
        assert job is not None and "hour='4'" in str(job.trigger) and "minute='40'" in str(job.trigger)
    finally:
        c.scheduler.shutdown()


# ====================================================================== B8: 마이그레이션 0004
def test_migration_0004_up_down_sqlite(tmp_path: Path) -> None:
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    from app.core.config import BACKEND_DIR
    from app.db.models import Base

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url

    def indexes(table: str) -> set[str]:
        eng = create_engine(url)
        try:
            return {str(i["name"]) for i in inspect(eng).get_indexes(table)}
        finally:
            eng.dispose()

    command.upgrade(cfg, "head")
    eng = create_engine(url)
    with eng.connect() as con:
        assert compare_metadata(MigrationContext.configure(con), Base.metadata) == []  # 모델과 같은 스키마
    eng.dispose()
    assert {"ix_fills_filled_at", "ix_fills_symbol"} <= indexes("fills")
    assert {"ix_chat_messages_role_id"} <= indexes("chat_messages")
    assert {"ix_agent_runs_started_at", "ix_agent_runs_status_id"} <= indexes("agent_runs")
    assert "ix_daily_prices_symbol" not in indexes("daily_prices")
    command.downgrade(cfg, "0003")
    assert "ix_daily_prices_symbol" in indexes("daily_prices")
    assert indexes("fills") == {"ix_fills_order_id"}
    command.upgrade(cfg, "head")
    assert "ix_fills_filled_at" in indexes("fills")


def test_memory_queries_use_new_indexes(tmp_path: Path) -> None:
    """기억 조회(내 메시지 최근 N개, 완료 세션 최근 N개)가 새 인덱스를 쓴다 (예전: 기본키를 거꾸로 훑음)."""
    from sqlalchemy import text

    from app.main import _migrate

    url = f"sqlite:///{tmp_path / 'kr.db'}"
    _migrate(url)
    db = Database(url)

    def plan(sql: str) -> str:
        with db.session() as s:
            return " ".join(str(r[-1]) for r in s.execute(text("EXPLAIN QUERY PLAN " + sql)))

    assert "ix_chat_messages_role_id" in plan(
        "SELECT id FROM chat_messages WHERE role = 'user' ORDER BY id DESC LIMIT 5"
    )
    assert "ix_agent_runs_status_id" in plan(
        "SELECT id FROM agent_runs WHERE status = 'DONE' ORDER BY id DESC LIMIT 3"
    )
    assert "ix_fills_symbol" in plan("SELECT id FROM fills WHERE symbol = '005930'")
