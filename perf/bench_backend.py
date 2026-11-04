#!/usr/bin/env python3
"""In-process, deterministic backend benchmark (no network, no .env).

Builds the real FastAPI app (`app.main.create_app`) with the test fakes (tests/fake_toss.FakeTossClient,
SimulatedClock) on a temporary file-backed SQLite DB (alembic migrations applied, like prod), seeds a
realistic data volume with the ORM models, then measures for every GET endpoint the UI uses:

  * wall time (median / p95 of N requests after warm-up, through Starlette's TestClient)
  * SQL statements per request (SQLAlchemy `before_cursor_execute` on the KR + US engines) and DB time
  * Toss API calls per request (FakeTossClient.calls)
  * payload bytes and rows returned

plus background-job costs (1-min intraday, 10-min refresh, scheduler dispatcher, indicator refresh)
and one agent session with a scripted fake LLM (respx), counting tool calls / SQL / Toss calls.

Run (from backend/ so the uv env + imports resolve):

    cd backend && uv run python ../perf/bench_backend.py            # -> docs/perf/raw/before/inprocess.json
    cd backend && uv run python ../perf/bench_backend.py --label after

Determinism: fixed clock (2026-11-02 10:00 KST, KR market open), fixed RNG seed, fixed volumes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import platform
import random
import shutil
import socket
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HERE))

# ----------------------------------------------------------------------------- safety guards
_ALLOWED = {"127.0.0.1", "localhost", "::1"}
_real_connect = socket.socket.connect
_real_cc = socket.create_connection


def _host(addr: Any) -> str | None:
    return str(addr[0]) if isinstance(addr, tuple) and addr else None


def _guard_connect(self: socket.socket, addr: Any) -> Any:
    h = _host(addr)
    if h is not None and h not in _ALLOWED:
        raise RuntimeError(f"bench: outbound network blocked ({h})")
    return _real_connect(self, addr)


def _guard_cc(addr: Any, *a: Any, **k: Any) -> socket.socket:
    h = _host(addr)
    if h is not None and h not in _ALLOWED:
        raise RuntimeError(f"bench: outbound network blocked ({h})")
    return _real_cc(addr, *a, **k)


socket.socket.connect = _guard_connect  # type: ignore[method-assign]
socket.create_connection = _guard_cc  # type: ignore[assignment]

from app.core.config import Settings  # noqa: E402

# never read the real .env, and ignore any exported app settings
Settings.model_config["env_file"] = None
for _name in list(Settings.model_fields):
    os.environ.pop(_name.upper(), None)

import httpx  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event, func, insert, select  # noqa: E402

from app.core.clock import KST, SimulatedClock  # noqa: E402
from app.db.models import (  # noqa: E402
    AgentDecision,
    AgentRun,
    ApiCache,
    AuditEvent,
    ChatMessage,
    DailyPrice,
    DailyReport,
    EquitySnapshot,
    Fill,
    LlmUsage,
    MemoryCard,
    Order,
    WatchlistItem,
)
from app.main import create_app  # noqa: E402
from endpoints import ALL_ENDPOINTS, count_items, percentile  # noqa: E402
import tests.fake_toss as fake_toss  # noqa: E402
from tests.fake_toss import FakeStock, FakeTossClient, FakeUsStock  # noqa: E402


def extend_fake_universe(n: int) -> None:
    """Grow the fake Toss universe (in this process only) so every seeded symbol is a 'real' listed stock.

    Without this, unknown symbols bypass the quote/info caches and inflate Toss-call counts.
    """
    i = 0
    while len(fake_toss.STOCKS) < n:
        st = FakeStock(f"9{i:05d}", f"합성종목{i:02d}", "KOSPI", 20_000 + 1_000 * i, 0.001, 1e8, 1e6, i / 7)
        fake_toss.STOCKS.append(st)
        fake_toss.BY[st.symbol] = st
        i += 1
    i = 0
    while len(fake_toss.US_STOCKS) < n:
        us = FakeUsStock(f"TK{i:03d}", f"합성미장{i:02d}", f"SYNTH CORP {i}", "NASDAQ", "STOCK",
                         50.0 + i, 0.001, 1e9, 1e6, i / 7)
        fake_toss.US_STOCKS.append(us)
        fake_toss.US_BY[us.symbol] = us
        i += 1

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=KST)  # Monday, KR regular session
US_SESSION_NOW = datetime(2026, 11, 3, 0, 30, tzinfo=KST)  # 10:30 ET Monday, US regular session
SINCE = datetime(2026, 8, 1, 0, 0, tzinfo=KST)
LLM_SETTINGS = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}
LLM_URL = "https://llm.test/v1/chat/completions"

VOLUME = {
    "chat_messages": 2000,
    "agent_runs": 300,
    "decisions_per_run": 7,
    "orders": 1000,
    "fills": 1000,
    "audit_events": 5000,
    "equity_snapshots": 2000,
    "price_symbols": 50,
    "price_days": 200,
    "llm_usage_per_run": 10,
    "llm_usage_chat": 150,
    "daily_reports": 60,
    "memory_cards": 20,
    "watchlist": 20,
    "positions": 8,
    "api_cache_rows": 200,
}
# payload lengths (chars of JSON) calibrated from the live Postgres at baseline (docs/perf/raw/before/db.json)
CHAT_MIX = [  # (role, kind, share, payload_len, content_len)
    ("analyst", "tool", 0.55, 340, 19),
    ("analyst", "text", 0.21, 3, 89),
    ("analyst", "decision", 0.15, 1320, 10),
    ("system", "text", 0.04, 14, 52),
    ("analyst", "summary", 0.02, 44, 395),
    ("broker", "fill", 0.01, 1170, 51),
    ("user", "text", 0.01, 2, 20),
    ("risk", "text", 0.01, 20, 48),
]
AUDIT_MIX = [  # (type, share, payload_len)
    ("SIGNAL", 0.60, 34),
    ("AGENT_RUN", 0.09, 118),
    ("AGENT_PAYLOAD", 0.08, 10500),
    ("SYSTEM", 0.05, 2),
    ("ORDER", 0.06, 635),
    ("FILL", 0.04, 51),
    ("FILLED", 0.04, 1077),
    ("CONFIG", 0.02, 84),
    ("RISK_BLOCK", 0.02, 87),
]


# ----------------------------------------------------------------------------- seed helpers
_POOL_RNG = random.Random(7)
_POOL = ["".join(_POOL_RNG.choice("가나다라마바사아자차카타파하abcdefghij0123456789 ") for _ in range(40))
         for _ in range(512)]
_TEXT = "".join(_POOL)


def blob(n: int, rng: random.Random) -> dict[str, Any]:
    """A JSON object whose serialized length is roughly n characters (deterministic for a given rng)."""
    if n <= 4:
        return {}
    items: list[dict[str, Any]] = []
    size = 12
    i = 0
    while size < n:
        items.append({"k": i, "v": _POOL[rng.randrange(len(_POOL))]})
        size += 60
        i += 1
    return {"items": items}


def text(n: int, rng: random.Random) -> str:
    start = rng.randrange(len(_TEXT) - n - 1)
    return _TEXT[start : start + n]


def weekdays(end: date, n: int) -> list[date]:
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def pick(mix: list[tuple[Any, ...]], rng: random.Random, share_idx: int) -> tuple[Any, ...]:
    r = rng.random()
    acc = 0.0
    for m in mix:
        acc += m[share_idx]
        if r <= acc:
            return m
    return mix[-1]


def seed_stack(c: Any, code: str, rng: random.Random) -> dict[str, int]:
    """Seed one market stack (KR or US) with VOLUME rows. Returns row counts."""
    if code == "KR":
        stocks: list[Any] = fake_toss.STOCKS[: VOLUME["price_symbols"]]
        open_utc, span_min, base_px, eq0 = 0, 390, 50_000.0, 10_000_000.0
    else:
        stocks = fake_toss.US_STOCKS[: VOLUME["price_symbols"]]
        open_utc, span_min, base_px, eq0 = 14 * 60 + 30, 390, 150.0, 10_000.0
    symbols = [x.symbol for x in stocks]
    names = {x.symbol: x.name for x in stocks}
    end_day = date(2026, 10, 30)
    price_days = weekdays(end_day, VOLUME["price_days"])
    act_days = [d for d in price_days if d >= SINCE.date()]

    def ts_on(d: date, minute: int) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(minutes=open_utc + minute)

    def rand_ts() -> datetime:
        return ts_on(rng.choice(act_days), rng.randrange(span_min)) + timedelta(seconds=rng.randrange(60))

    counts: dict[str, int] = {}
    with c.db.session() as s:
        # daily prices
        rows = []
        for sym in symbols:
            px = base_px * rng.uniform(0.3, 3)
            for d in price_days:
                px *= 1 + rng.uniform(-0.02, 0.021)
                rows.append(
                    {
                        "symbol": sym, "day": d, "name": names[sym], "market": "KOSPI" if code == "KR" else "NASDAQ",
                        "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": 1e6,
                        "value": px * 1e6, "market_cap": px * 1e7, "listed_shares": 1e7,
                    }
                )
        s.execute(insert(DailyPrice), rows)
        counts["daily_prices"] = len(rows)
        # watchlist
        existing = set(s.scalars(select(WatchlistItem.symbol)))
        wl = [x for x in symbols[: VOLUME["watchlist"]] if x not in existing]
        s.execute(
            insert(WatchlistItem),
            [{"symbol": x, "name": names[x], "active": True, "created_at": NOW - timedelta(days=30)} for x in wl],
        )
        counts["watchlist"] = len(existing) + len(wl)
        # agent runs (+ decisions)
        run_times = sorted(rand_ts() for _ in range(VOLUME["agent_runs"]))
        base_run = (s.scalar(select(func.max(AgentRun.id))) or 0) + 1
        runs = []
        for i, t in enumerate(run_times):
            ok = rng.random() > 0.05
            runs.append(
                {
                    "id": base_run + i, "trigger": rng.choice(["morning", "hourly", "hourly", "manual"]),
                    "mode": "llm-tools", "model": "test-model", "status": "DONE" if ok else "ERROR",
                    "started_at": t, "finished_at": t + timedelta(minutes=3), "market_view": text(190, rng),
                    "summary": text(350, rng), "error": None if ok else text(100, rng),
                    "usage": {"prompt_tokens": rng.randrange(20_000, 90_000), "completion_tokens": 2000,
                              "total_tokens": 0, "calls": 12},
                }
            )
        s.execute(insert(AgentRun), runs)
        counts["agent_runs"] = len(runs)
        # orders (+ fills)
        base_order = (s.scalar(select(func.max(Order.id))) or 0) + 1
        order_rows = []
        for i in range(VOLUME["orders"]):
            r = rng.choice(runs) if rng.random() < 0.8 else None
            t = (r["started_at"] + timedelta(minutes=2)) if r else rand_ts()
            order_rows.append((t, r["id"] if r else None))
        order_rows.sort(key=lambda x: x[0])
        orders = []
        for i, (t, run_id) in enumerate(order_rows):
            u = rng.random()
            status = "FILLED" if u < 0.9 else "CANCELED" if u < 0.95 else "REJECTED" if u < 0.995 else "SUBMITTED"
            sym = rng.choice(symbols)
            px = base_px * rng.uniform(0.5, 2)
            qty = rng.randrange(1, 50)
            orders.append(
                {
                    "id": base_order + i, "client_order_id": f"bench-{code}-{i:06d}", "broker_order_id": f"P{i + 1:08d}",
                    "symbol": sym, "side": rng.choice(["BUY", "SELL"]), "order_type": "MARKET", "quantity": qty,
                    "limit_price": None, "status": status, "filled_qty": qty if status == "FILLED" else 0,
                    "avg_fill_price": px if status == "FILLED" else None, "strategy_id": None, "run_id": run_id,
                    "fill_basis": "NOW", "trade_day": t.astimezone(KST).date(), "reason": text(60, rng),
                    "reject_reason": "max_order_amount" if status == "REJECTED" else None,
                    "created_at": t, "updated_at": t + timedelta(seconds=5),
                }
            )
        s.execute(insert(Order), orders)
        counts["orders"] = len(orders)
        filled = [o for o in orders if o["status"] == "FILLED"]
        fills = []
        for o in filled:
            fills.append({"order_id": o["id"], "symbol": o["symbol"], "side": o["side"], "quantity": o["quantity"],
                          "price": o["avg_fill_price"], "fee": 10.0, "tax": 0.0,
                          "filled_at": o["created_at"] + timedelta(seconds=3)})
        while len(fills) < VOLUME["fills"]:
            o = rng.choice(filled)
            fills.append({"order_id": o["id"], "symbol": o["symbol"], "side": o["side"], "quantity": 1,
                          "price": o["avg_fill_price"], "fee": 1.0, "tax": 0.0,
                          "filled_at": o["created_at"] + timedelta(seconds=4)})
        s.execute(insert(Fill), fills)
        counts["fills"] = len(fills)
        # decisions
        order_by_run: dict[int, list[int]] = {}
        for o in orders:
            if o["run_id"]:
                order_by_run.setdefault(o["run_id"], []).append(o["id"])
        decs = []
        for r in runs:
            ids = list(order_by_run.get(r["id"], []))
            for _ in range(VOLUME["decisions_per_run"]):
                u = rng.random()
                action = "HOLD" if u < 0.7 else "BUY" if u < 0.9 else "SELL"
                sym = rng.choice(symbols)
                decs.append(
                    {
                        "run_id": r["id"], "symbol": sym, "name": names[sym], "action": action, "quantity": 0,
                        "target_weight": 0.05, "order_type": "MARKET", "limit_price": None,
                        "confidence": rng.random(), "rationale": text(190, rng),
                        "evidence": blob(414, rng)["items"],
                        "order_id": ids.pop() if action != "HOLD" and ids else None, "outcome": "",
                        "created_at": r["started_at"] + timedelta(minutes=2),
                    }
                )
        s.execute(insert(AgentDecision), decs)
        counts["agent_decisions"] = len(decs)
        # chat
        chat = []
        for _ in range(VOLUME["chat_messages"]):
            role, kind, _sh, plen, clen = pick(CHAT_MIX, rng, 2)
            r = rng.choice(runs) if role in ("analyst", "broker", "risk") else None
            t = (r["started_at"] + timedelta(seconds=rng.randrange(180))) if r else rand_ts()
            chat.append({"ts": t, "run_id": r["id"] if r else None, "role": role, "kind": kind,
                         "content": text(clen, rng), "payload": blob(plen, rng)})
        chat.sort(key=lambda x: x["ts"])
        s.execute(insert(ChatMessage), chat)
        counts["chat_messages"] = len(chat)
        # audit
        audit = []
        for _ in range(VOLUME["audit_events"]):
            typ, _sh, plen = pick(AUDIT_MIX, rng, 1)
            audit.append({"ts": rand_ts(), "type": typ, "symbol": rng.choice(symbols) if typ != "SYSTEM" else None,
                          "message": text(20, rng), "payload": blob(plen, rng)})
        audit.sort(key=lambda x: x["ts"])
        s.execute(insert(AuditEvent), audit)
        counts["audit_events"] = len(audit)
        # equity
        eq = sorted(rand_ts() for _ in range(VOLUME["equity_snapshots"]))
        s.execute(insert(EquitySnapshot),
                  [{"ts": t, "equity": eq0 * rng.uniform(0.95, 1.08), "cash": eq0 * 0.4} for t in eq])
        counts["equity_snapshots"] = len(eq)
        # llm usage
        usage = []
        for r in runs:
            for _ in range(VOLUME["llm_usage_per_run"]):
                usage.append({"ts": r["started_at"], "kind": "session", "run_id": r["id"], "model": "test-model",
                              "calls": 1, "input_tokens": rng.randrange(5000, 40000), "cached_tokens": 3000,
                              "output_tokens": 300, "max_input_tokens": 40000, "source": "live"})
        for _ in range(VOLUME["llm_usage_chat"]):
            usage.append({"ts": rand_ts(), "kind": "chat", "run_id": None, "model": "test-model", "calls": 1,
                          "input_tokens": 8000, "cached_tokens": 0, "output_tokens": 200,
                          "max_input_tokens": 8000, "source": "live"})
        s.execute(insert(LlmUsage), usage)
        counts["llm_usage"] = len(usage)
        # daily reports (~17 KB payload each, like prod)
        rep_days = act_days[-VOLUME["daily_reports"]:]
        s.execute(insert(DailyReport),
                  [{"day": d, "payload": {"day": d.isoformat(), **blob(17000, rng)}, "created_at": ts_on(d, 400)}
                   for d in rep_days])
        counts["daily_reports"] = len(rep_days)
        # memory cards
        kinds = ["watch", "focus", "avoid", "note"]
        cards = []
        for i in range(VOLUME["memory_cards"]):
            k = kinds[i % 4]
            sym = symbols[i] if k != "note" else None
            cards.append({"kind": k, "symbol": sym, "name": names.get(sym or "", None), "text": text(80, rng),
                          "active": i % 5 != 0, "expires_on": None, "created_at": NOW - timedelta(days=i),
                          "updated_at": NOW - timedelta(days=i)})
        s.execute(insert(MemoryCard), cards)
        counts["memory_cards"] = len(cards)
        if code == "KR":
            s.execute(insert(ApiCache), [{"key": f"dart:fin:{i:08d}", "payload": blob(49000, rng),
                                          "fetched_at": NOW - timedelta(hours=i)}
                                         for i in range(VOLUME["api_cache_rows"])])
            counts["api_cache"] = VOLUME["api_cache_rows"]
    # paper account: every order ever placed lives in one JSON blob (like prod)
    st = c.broker.state
    st["orders"], st["client_ids"] = {}, {}
    for o in orders:
        bid = o["broker_order_id"]
        st["orders"][bid] = {
            "client_order_id": o["client_order_id"], "symbol": o["symbol"], "side": o["side"],
            "order_type": "MARKET", "quantity": o["quantity"], "limit_price": None,
            "status": "FILLED" if o["status"] == "SUBMITTED" else o["status"], "filled_qty": o["filled_qty"],
            "avg_fill_price": o["avg_fill_price"], "created_at": o["created_at"].isoformat(),
            "reserved": 0.0, "fill_basis": "NOW", "trade_day": o["trade_day"].isoformat(),
        }
        st["client_ids"][o["client_order_id"]] = bid
    st["seq"] = len(orders)
    pos_syms = symbols[: VOLUME["positions"]]
    st["positions"] = {
        x: {"qty": 10 + i, "avg_price": base_px, "opened_day": "2026-10-01", "name": names[x]}
        for i, x in enumerate(pos_syms)
    }
    st["cash"] = eq0 * 0.4
    c.broker._save()
    counts["paper_state_bytes"] = len(json.dumps(st, ensure_ascii=False))
    c.kv.set("account_source", {"source": "toss", "since": SINCE.isoformat(), "purged": 4})
    return counts


# ----------------------------------------------------------------------------- instrumentation
class SqlCounter:
    def __init__(self) -> None:
        self.n: dict[str, int] = {}
        self.ms: dict[str, float] = {}
        self._t0: dict[int, float] = {}
        self.capture: list[str] | None = None

    def attach(self, engine: Any, label: str) -> None:
        self.n[label] = 0
        self.ms[label] = 0.0

        @event.listens_for(engine, "before_cursor_execute")
        def _before(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
            self.n[label] += 1
            if self.capture is not None:
                self.capture.append(f"[{label}] " + " ".join(statement.split()))
            self._t0[id(cursor)] = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def _after(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
            t0 = self._t0.pop(id(cursor), None)
            if t0 is not None:
                self.ms[label] += (time.perf_counter() - t0) * 1000

    def snap(self) -> tuple[dict[str, int], dict[str, float]]:
        return dict(self.n), dict(self.ms)


def diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {k: b[k] - a.get(k, 0) for k in b}


class Probe:
    """Measures one callable: wall ms, SQL statements (per DB), DB ms, Toss calls."""

    def __init__(self, sql: SqlCounter, fake: FakeTossClient) -> None:
        self.sql, self.fake = sql, fake

    def run(self, fn: Any) -> tuple[Any, dict[str, Any]]:
        n0, ms0 = self.sql.snap()
        c0 = len(self.fake.calls)
        t0 = time.perf_counter()
        res = fn()
        wall = (time.perf_counter() - t0) * 1000
        n1, ms1 = self.sql.snap()
        calls = self.fake.calls[c0:]
        by_db = diff(n0, n1)
        kinds: dict[str, int] = {}
        for x in calls:
            kinds[x] = kinds.get(x, 0) + 1
        return res, {
            "wall_ms": wall,
            "sql": sum(by_db.values()),
            "sql_by_db": by_db,
            "db_ms": sum(diff(ms0, ms1).values()),
            "toss_calls": len(calls),
            "toss_by_api": kinds,
        }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    walls = [x["wall_ms"] for x in samples]
    return {
        "median_ms": round(statistics.median(walls), 3),
        "p95_ms": round(percentile(walls, 95), 3),
        "min_ms": round(min(walls), 3),
        "sql_statements": int(statistics.median(x["sql"] for x in samples)),
        "sql_by_db": {k: int(statistics.median(x["sql_by_db"][k] for x in samples)) for k in samples[0]["sql_by_db"]},
        "db_ms_median": round(statistics.median(x["db_ms"] for x in samples), 3),
        "toss_calls_mean": round(statistics.fmean(x["toss_calls"] for x in samples), 3),
    }


# ----------------------------------------------------------------------------- agent session (fake LLM)
def _tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _resp(content: str | None, calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return httpx.Response(200, json={
        "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    })


def scripted_session() -> list[httpx.Response]:
    """~27 tool calls in 6 LLM turns — the live average was 1199 tool messages / 44 sessions ≈ 27."""
    i = iter(range(1, 100))
    syms = ["005930", "000660", "035420", "005380", "068270", "247540"]
    turns = [
        _resp("시장부터", [_tc("get_market_overview", {}, next(i)), _tc("get_portfolio", {}, next(i)),
                         _tc("get_trade_history", {}, next(i))]),
        _resp("후보 찾기", [_tc("screen_stocks", {"sort_by": k}, next(i))
                         for k in ("trading_value", "change_1d", "foreigner_net_buy", "institution_net_buy")]),
        _resp("종목 보기", [_tc("get_price_history", {"symbol": s, "days": 60}, next(i)) for s in syms]),
        _resp("상태·수급", [_tc("get_stock_status", {"symbol": s}, next(i)) for s in syms[:4]]
              + [_tc("get_supply_demand", {"symbol": s, "days": 10}, next(i)) for s in syms[:4]]),
        _resp("재무·뉴스·섹터", [_tc("get_financials", {"symbol": syms[0]}, next(i)),
                            _tc("search_news", {"query": "삼성전자", "limit": 5}, next(i)),
                            _tc("get_disclosures", {"symbol": syms[0], "days": 30}, next(i)),
                            _tc("explore_sector", {"query": "반도체"}, next(i))]),
        _resp(None, [_tc("submit_decisions", {
            "market_view": "중립", "summary": "벤치마크 세션",
            "decisions": [
                {"symbol": "005930", "action": "BUY", "target_weight": 0.05, "confidence": 0.7, "rationale": "a"},
                {"symbol": "000660", "action": "SELL", "quantity": 1, "confidence": 0.6, "rationale": "b"},
                {"symbol": "035420", "action": "HOLD", "confidence": 0.3, "rationale": "c"},
            ]}, next(i))]),
    ]
    return turns + [_resp("끝")] * 3


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="measured requests per endpoint")
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--label", default="before", help="before | after | any tag")
    ap.add_argument("--out", default="", help="default: docs/perf/raw/<label>/inprocess.json")
    ap.add_argument("--keep-db", action="store_true", help="keep the temp DB dir for inspection")
    a = ap.parse_args()
    a.out = a.out or str(ROOT / "docs/perf/raw" / a.label / "inprocess.json")

    tmp = Path(tempfile.mkdtemp(prefix="perf_bench_"))
    rng = random.Random(42)
    clock = SimulatedClock(NOW)
    extend_fake_universe(VOLUME["price_symbols"])
    fake = FakeTossClient(clock)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp / 'kr.db'}",
        data_dir=tmp,
        auth_enabled=False,
        enable_scheduler=False,
        log_level="ERROR",
        **LLM_SETTINGS,
    )
    t_build = time.perf_counter()
    app = create_app(settings, toss_client=fake, clock=clock)
    build_ms = (time.perf_counter() - t_build) * 1000
    import logging

    logging.getLogger().setLevel(logging.CRITICAL)
    cs = app.state.containers
    kr, us = cs["KR"], cs.get("US")
    t_seed = time.perf_counter()
    seed = {"KR": seed_stack(kr, "KR", rng)}
    if us is not None:
        seed["US"] = seed_stack(us, "US", rng)
    seed_ms = (time.perf_counter() - t_seed) * 1000

    sql = SqlCounter()
    sql.attach(kr.db.engine, "kr")
    if us is not None:
        sql.attach(us.db.engine, "us")
    probe = Probe(sql, fake)
    out: dict[str, Any] = {
        "schema": "inprocess/v1",
        "taken_at": datetime.now(UTC).isoformat(),
        "env": {"python": platform.python_version(), "platform": platform.platform(),
                "db": "sqlite file (alembic head)", "clock": NOW.isoformat()},
        "params": {"n": a.n, "warmup": a.warmup, "volume": VOLUME},
        "seed_rows": seed,
        "timings": {"create_app_ms": round(build_ms, 1), "seed_ms": round(seed_ms, 1)},
    }

    with TestClient(app) as client:
        # ------------------------------------------------------------ endpoints
        eps: dict[str, Any] = {}
        for key, path, source, note in ALL_ENDPOINTS:
            r, cold = probe.run(lambda p=path: client.get(p))  # noqa: B023
            for _ in range(a.warmup):
                client.get(path)
            samples = []
            body = r
            for _ in range(a.n):
                body, m = probe.run(lambda p=path: client.get(p))  # noqa: B023
                samples.append(m)
            try:
                items = count_items(body.json())
            except ValueError:
                items = None
            sql.capture = []
            client.get(path)
            stmts, sql.capture = sql.capture, None
            repeated = [
                {"count": n, "statement": st[:160]}
                for st, n in Counter(stmts).most_common(3) if n > 1
            ]
            eps[key] = {
                "path": path, "source": source, "status": body.status_code, "bytes": len(body.content),
                "bytes_gzip": len(gzip.compress(body.content, 6)),
                "items": items, **summarize(samples), "repeated_statements": repeated,
                "cold": {"wall_ms": round(cold["wall_ms"], 3), "sql": cold["sql"], "toss_calls": cold["toss_calls"],
                         "toss_by_api": cold["toss_by_api"]},
            }
            e = eps[key]
            print(f"{key:22s} {e['median_ms']:8.2f}ms sql={e['sql_statements']:4d} toss={e['toss_calls_mean']:5.2f} "
                  f"cold_sql={cold['sql']:4d} cold_toss={cold['toss_calls']:3d} bytes={e['bytes']:>8d} items={items}")
        out["endpoints"] = eps

        # ------------------------------------------------------------ background jobs (per invocation)
        def cold_caches(c: Any) -> None:
            c.engine._health = (True, -1e9)  # toss_health re-probes after 60 s -> every 1-min tick
            c.toss._quote_cache.clear()  # 5 s quote cache -> expired at every 1-min tick

        bg: dict[str, Any] = {}

        def measure_bg(name: str, c: Any, fn: Any, reps: int = 5) -> None:
            samples = []
            for _ in range(reps):
                cold_caches(c)
                _, m = probe.run(lambda: client.portal.call(fn))  # noqa: B023
                samples.append(m)
            bg[name] = {**summarize(samples), "toss_by_api": samples[-1]["toss_by_api"]}
            print(f"bg {name:26s} {bg[name]['median_ms']:8.2f}ms sql={bg[name]['sql_statements']} "
                  f"toss={bg[name]['toss_calls_mean']} {bg[name]['toss_by_api']}")

        async def kr_due() -> Any:
            return kr.scheduler.due(kr.clock.now())

        measure_bg("kr_intraday_1min", kr, kr.engine.intraday)
        measure_bg("kr_sync_data_10min", kr, kr.engine.sync_data)
        measure_bg("kr_scheduler_due_1min", kr, kr_due)

        async def ind_refresh() -> Any:
            return await kr.indicators._fetch()

        measure_bg("indicators_refresh_60s", kr, ind_refresh, reps=3)
        if us is not None:
            clock.set(US_SESSION_NOW)

            async def us_due() -> Any:
                return us.scheduler.due(us.clock.now())

            async def us_refresh_tick() -> Any:  # REFRESH_IN_TICK: sync every 10 min near the session
                return await us.engine.sync_data()

            measure_bg("us_intraday_1min", us, us.engine.intraday)
            measure_bg("us_sync_data_10min", us, us_refresh_tick)
            measure_bg("us_scheduler_due_1min", us, us_due)
            clock.set(NOW)
        out["background"] = bg

        # ------------------------------------------------------------ agent session with scripted LLM
        sess: dict[str, Any] = {}
        with respx.mock(assert_all_called=False) as rx:
            route = rx.post(LLM_URL).mock(side_effect=scripted_session())
            with kr.db.session() as s:
                chat0 = s.scalar(select(func.count()).select_from(ChatMessage)) or 0
                audit0 = s.scalar(select(func.count()).select_from(AuditEvent)) or 0
            sql.capture = []
            run_id, m = probe.run(lambda: client.portal.call(kr.runner.run, "manual"))
            stmts, sql.capture = sql.capture, None
            with kr.db.session() as s:
                tools = s.scalar(select(func.count()).select_from(ChatMessage)
                                 .where(ChatMessage.run_id == run_id, ChatMessage.kind == "tool")) or 0
                chat1 = s.scalar(select(func.count()).select_from(ChatMessage)) or 0
                audit1 = s.scalar(select(func.count()).select_from(AuditEvent)) or 0
                run = s.get(AgentRun, run_id) if run_id else None
            req_bytes = [len(c.request.content) for c in route.calls]
            sess["llm_scripted"] = {
                "run_id": run_id, "status": run.status if run else None, "wall_ms": round(m["wall_ms"], 1),
                "llm_calls": route.call_count, "tool_calls": tools, "sql_statements": m["sql"],
                "db_ms": round(m["db_ms"], 1), "toss_calls": m["toss_calls"], "toss_by_api": m["toss_by_api"],
                "llm_request_bytes": req_bytes, "llm_request_bytes_total": sum(req_bytes),
                "chat_rows_written": chat1 - chat0, "audit_rows_written": audit1 - audit0,
                "repeated_statements": [
                    {"count": n, "statement": st[:160]} for st, n in Counter(stmts).most_common(6) if n > 1
                ],
            }
            print("agent llm session", sess["llm_scripted"])
        llm = kr.runner.llm
        kr.runner.llm = None
        cold_caches(kr)
        run_id, m = probe.run(lambda: client.portal.call(kr.runner.run, "manual"))
        kr.runner.llm = llm
        sess["rule_based"] = {"run_id": run_id, "wall_ms": round(m["wall_ms"], 1), "sql_statements": m["sql"],
                              "db_ms": round(m["db_ms"], 1), "toss_calls": m["toss_calls"]}
        print("agent rule session", sess["rule_based"])
        out["agent_session"] = sess

        with kr.db.session() as s:
            from app.db.models import PaperAccountState

            row = s.get(PaperAccountState, 1)
            out["paper_state_bytes_after"] = len(json.dumps(row.state, ensure_ascii=False)) if row else None

    out["db_file_bytes"] = {p.name: p.stat().st_size for p in tmp.iterdir() if p.is_file() and ".db" in p.name}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {a.out}")
    if a.keep_db:
        print(f"kept {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
