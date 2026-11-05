"""LLM 요금 장부 (D-66): 호출마다 모델·토큰·실제 요금, 장부 한 곳으로 모으기, 월별·날짜별 묶음, 공식 기록 대조.

실제 유료 호출은 하지 않는다 — 응답·공식 기록은 모두 respx 가짜.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text

from app.agent import billing
from app.agent.costs import backfill, charge, ledger_view, move_usage, price_list, record_call
from app.agent.llm import ChatResult, LLMClient, parse_chat_usage, parse_responses
from app.core.clock import KST
from app.core.config import Settings
from app.db.models import LlmUsage
from app.db.session import Database
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

LLM_URL = "https://llm.test/v1/chat/completions"
D = date(2026, 11, 2)
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}
TS = datetime(2026, 11, 4, 10, tzinfo=KST)


def _resp(prompt: int, completion: int, calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": None if calls else "끝"}
    if calls:
        msg["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        },
    )


def _tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _rows(db: Database) -> list[LlmUsage]:
    with db.session() as s:
        return list(s.scalars(select(LlmUsage).order_by(LlmUsage.id)))


def _db() -> Database:
    db = Database("sqlite://")
    db.create_all()
    return db


def _llm(base: str, model: str, profile: str = "paid") -> LLMClient:
    return LLMClient(base, model, "", profile=profile)


OPENAI = "https://api.openai.com/v1"
GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
OPENROUTER = "https://openrouter.ai/api/v1"


# ====================================================================== 판단·답장 기록
@respx.mock
async def test_session_records_each_call_with_cache_estimate(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    respx.post(LLM_URL).mock(
        side_effect=[
            _resp(1000, 50, [_tc("get_portfolio", {}, 1)]),
            _resp(1600, 40, [_tc("submit_decisions", {"summary": "관망", "decisions": []}, 2)]),
        ]
    )
    run_id = await c.runner.run("manual")
    rows = _rows(c.db)
    assert [(r.kind, r.run_id, r.input_tokens, r.cached_tokens, r.output_tokens) for r in rows] == [
        ("session", run_id, 1000, 0, 50),
        ("session", run_id, 1600, 1000, 40),  # 예전 추정(바로 전 호출 입력)은 그대로 남긴다
    ]
    r = rows[0]
    assert (r.model, r.source, r.market, r.strategy, r.profile) == ("test-model", "live", "KR", "", "paid")
    # 알 수 없는 유료 주소(gateway)의 단가표 밖 모델 → 0 원으로 치지 않고 '단가 모름'
    assert (r.provider, r.cost_usd, r.cost_source, r.cache_read_tokens) == ("gateway", None, "unpriced", None)


async def test_chat_reply_is_recorded(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)

    async def fake_chat(*a: Any, **k: Any) -> ChatResult:
        return ChatResult(
            content="답", usage={"prompt_tokens": 3000, "completion_tokens": 120, "total_tokens": 3120}
        )

    c.runner.llm.chat = fake_chat
    await c.runner.answer("오늘 어때?")
    assert [(r.kind, r.input_tokens, r.output_tokens) for r in _rows(c.db)] == [("chat", 3000, 120)]


# ====================================================================== 요금 매기기
def _openai_res(tier: str = "default", w12: int = 0) -> ChatResult:
    return parse_responses(
        {
            "id": "resp_1",
            "model": "gpt-6.1-sol-2026-10-01",
            "service_tier": tier,
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {
                "input_tokens": 10_000,  # 캐시 읽기·쓰기 포함
                "input_tokens_details": {
                    "cached_tokens": 6_000,
                    "cache_write_tokens": 2_000,
                    **({"cache_write_tokens_details": {"ttl_12h_tokens": w12}} if w12 else {}),
                },
                "output_tokens": 500,  # 추론 포함
                "output_tokens_details": {"reasoning_tokens": 300},
                "total_tokens": 10_500,
            },
        }
    )


def test_openai_responses_usage_is_priced_exactly() -> None:
    db = _db()
    res = _openai_res()
    assert res.usage["cached_tokens"] == 6_000 and res.usage["cache_write_tokens"] == 2_000
    assert res.meta == {"id": "resp_1", "model": "gpt-6.1-sol-2026-10-01", "service_tier": "default"}
    ch = record_call(db, TS, kind="session", run_id=1, llm=_llm(OPENAI, "gpt-6.1-sol"), res=res, market="KR")
    # Sol: 입력 $2 · 캐시 읽기 $0.10 · 출력 $10 /1M, 30분 캐시 쓰기는 입력의 1.25배
    #   나머지 2,000 × 2 + 읽기 6,000 × 0.10 + 쓰기 2,000 × 2.5 + 출력 500 × 10
    want = (2_000 * 2 + 6_000 * 0.10 + 2_000 * 2.5 + 500 * 10) / 1e6
    assert ch is not None and ch.source == "priced" and ch.cost == pytest.approx(want)
    r = _rows(db)[0]
    assert (r.provider, r.served_model, r.response_id) == ("openai", "gpt-6.1-sol-2026-10-01", "resp_1")
    assert (r.cache_read_tokens, r.cache_write_tokens, r.reasoning_tokens) == (6_000, 2_000, 300)
    assert r.cost_usd == pytest.approx(want) and r.cost_source == "priced"


def test_openai_tiers_and_12h_cache_writes() -> None:
    sol = _llm(OPENAI, "gpt-6.1-sol")
    base = (2_000 * 2 + 6_000 * 0.10 + 2_000 * 2.5 + 500 * 10) / 1e6
    flex = record_call(_db(), TS, kind="chat", run_id=None, llm=sol, res=_openai_res("flex"))
    assert flex is not None and flex.cost == pytest.approx(base * 0.5)  # Flex = Batch 요금
    fast = record_call(_db(), TS, kind="chat", run_id=None, llm=sol, res=_openai_res("priority"))
    assert fast is not None and (fast.cost, fast.source) == (None, "unpriced")  # 별도 요금표
    w12 = record_call(_db(), TS, kind="chat", run_id=None, llm=sol, res=_openai_res(w12=1_000))
    # 쓰기 2,000 중 12시간 1,000 (2.5배) + 30분 1,000 (1.25배)
    want = (2_000 * 2 + 6_000 * 0.10 + 1_000 * 2 * 2.5 + 1_000 * 2 * 1.25 + 500 * 10) / 1e6
    assert w12 is not None and w12.cost == pytest.approx(want)


def test_openrouter_reported_cost_is_used_as_is() -> None:
    usage, cost = parse_chat_usage(
        {
            "prompt_tokens": 2_000,
            "completion_tokens": 300,
            "total_tokens": 2_300,
            "cost": 0.00123,
            "is_byok": False,
            "prompt_tokens_details": {"cached_tokens": 1_500, "cache_write_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 120},
        }
    )
    assert cost == 0.00123
    assert (usage["cached_tokens"], usage["cache_write_tokens"], usage["reasoning_tokens"]) == (1_500, 0, 120)
    db = _db()
    res = ChatResult(
        content="", usage=usage, cost=cost, meta={"id": "gen-1", "model": "deepseek/deepseek-v9"}
    )
    llm = _llm(OPENROUTER, "deepseek/deepseek-v9")
    ch = record_call(db, TS, kind="session", run_id=3, llm=llm, res=res, market="US")
    assert ch is not None and (ch.cost, ch.source) == (0.00123, "reported")
    r = _rows(db)[0]
    assert (r.provider, r.market, r.response_id, r.cache_read_tokens) == ("openrouter", "US", "gen-1", 1_500)
    # 무료 모델은 OpenRouter 가 0 을 적어 준다 → 0 원 '보고'
    free = record_call(db, TS, kind="chat", run_id=None, llm=llm, res=ChatResult("", usage=usage, cost=0.0))
    assert free is not None and (free.cost, free.source) == (0.0, "reported")


def test_local_server_is_free_and_unknown_models_are_unpriced() -> None:
    usage = {"prompt_tokens": 5_000, "completion_tokens": 200}
    local = _llm("http://gpu-box.local:8000/v1", "qwen@gpu-box.local", profile="free")
    assert local.provider == "local"
    assert charge("local", local.model, usage, None, D).source == "free"
    gw = _llm("https://gateway.example/v1", "mystery-model", profile="paid")
    assert gw.provider == "gateway" and charge("gateway", gw.model, usage, None, D).cost is None
    # 게이트웨이라도 단가표 모델이면 공식 단가로 (앞부분·날짜 꼬리는 떼고 찾는다)
    assert charge("gateway", "openai/gpt-6.1-terra", usage, None, D).cost == pytest.approx(
        (5_000 * 1 + 200 * 5) / 1e6
    )


def test_google_hidden_thinking_tokens_are_billed_as_output() -> None:
    db = _db()
    usage = {
        "prompt_tokens": 1_000,
        "completion_tokens": 100,
        "total_tokens": 1_500,
    }  # 생각 400 이 출력에 없음
    llm = _llm(GOOGLE, "gemini-3.8-flash")
    ch = record_call(db, TS, kind="newsroom", run_id=None, llm=llm, res=ChatResult("", usage=usage))
    assert ch is not None and ch.cost == pytest.approx((1_000 * 0.75 + 500 * 3.75) / 1e6)
    assert _rows(db)[0].reasoning_tokens == 400
    # 출력에 이미 들어 있으면(합계가 맞으면) 더하지 않는다
    same = {"prompt_tokens": 1_000, "completion_tokens": 500, "total_tokens": 1_500}
    assert charge("google", "gemini-3.8-flash", same, None, date(2026, 11, 4)).cost == ch.cost
    # 2027-01-01 부터 인상된 단가
    later = charge("google", "gemini-3.8-flash", same, None, date(2027, 1, 1)).cost
    assert later == pytest.approx(ch.cost * 2)


# ====================================================================== 예전 기록·장부 모으기
def _legacy(db: Database, model: str, inp: int, out: int, cached: int = 0, ts: datetime = TS) -> None:
    with db.session() as s:
        s.add(
            LlmUsage(
                ts=ts,
                kind="session",
                run_id=1,
                model=model,
                calls=1,
                input_tokens=inp,
                cached_tokens=cached,
                output_tokens=out,
                max_input_tokens=inp,
                source="live",
            )
        )


def test_legacy_rows_are_backfilled() -> None:
    db = _db()
    _legacy(db, "Qwen/Qwen-Next@gpu-box.local", 10_000, 500)
    _legacy(db, "gpt-6.1-sol", 10_000, 500, cached=6_000)
    _legacy(db, "mystery", 10, 1)
    assert backfill(db) == 3 and backfill(db) == 0  # 한 번만
    q, sol, unknown = _rows(db)
    assert (q.provider, q.profile, q.cost_usd, q.cost_source) == ("local", "free", 0.0, "free")
    assert (sol.provider, sol.profile, sol.cost_source) == ("openai", "paid", "estimated")
    assert sol.cost_usd == pytest.approx((4_000 * 2 + 6_000 * 0.10 + 500 * 10) / 1e6)
    assert (unknown.cost_usd, unknown.cost_source) == (None, "unpriced")


def test_move_usage_into_ledger_once() -> None:
    ledger, us = _db(), _db()
    _legacy(us, "gpt-6.1-sol", 1_000, 100)
    _legacy(us, "gpt-6.1-sol", 2_000, 100, ts=datetime(2026, 11, 4, 11, tzinfo=KST))
    assert move_usage(us, ledger, "US") == 2
    assert _rows(us) == []  # 옮긴 뒤 지운다
    rows = _rows(ledger)
    assert [(r.market, r.strategy, r.cost_source) for r in rows] == [("US", "", "estimated")] * 2
    assert all(r.origin and r.origin.startswith("US::") for r in rows)
    assert move_usage(us, ledger, "US") == 0 and len(_rows(ledger)) == 2
    lab = _db()
    _legacy(lab, "q@gpu-box.local", 500, 10)
    assert move_usage(lab, ledger, "KR", "lynch") == 1
    assert _rows(ledger)[-1].strategy == "lynch" and _rows(ledger)[-1].cost_source == "free"


def test_backfill_tags_old_rows_with_their_market() -> None:
    db = _db()
    _legacy(db, "gpt-6.1-sol", 1_000, 100)
    with db.session() as s:
        s.add(
            LlmUsage(
                ts=TS, kind="newsroom", model="q@gpu-box.local", calls=1, input_tokens=5, output_tokens=1
            )
        )
    backfill(db, "KR")
    session, news = _rows(db)
    assert (session.market, news.market) == ("KR", "")  # 뉴스 룸은 공용
    assert backfill(db, "KR") == 0 and _rows(db)[0].market == "KR"


def test_disabled_lab_dbs_are_moved_at_boot(tmp_path: Path) -> None:
    from app.container import move_lab_usage, scenario_db_url
    from app.db.migrate import migrate

    url = f"sqlite:///{tmp_path / 'kr.db'}"
    migrate(url)
    c = make_container(tmp_path, at(D, 10), create_schema=False, database_url=url)
    assert c.lab is not None and c.lab.stacks == {}  # 켜 둔 전략 없음
    lab_url = scenario_db_url(c.settings, "KR", "graham")
    migrate(lab_url)
    lab = Database(lab_url)
    _legacy(lab, "q@gpu-box.local", 500, 10)
    lab.engine.dispose()
    assert move_lab_usage(c) == 1 and move_lab_usage(c) == 0
    row = _rows(c.db)[-1]
    assert (row.market, row.strategy, row.cost_source) == ("KR", "graham", "free")


def test_us_and_lab_calls_go_to_the_kr_ledger(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **LLM) as client:
        kr = client.app.state.containers["KR"]  # type: ignore[attr-defined]
        us = client.app.state.containers["US"]  # type: ignore[attr-defined]
        assert us.runner.usage_db is kr.db and us.runner.usage_market == "US"
        kr.lab.set_enabled(["lynch", "buffett"])
        kr.lab.ensure()
        assert set(kr.lab.stacks) == {"lynch", "buffett"}
        for sid, st in kr.lab.stacks.items():
            assert st.runner.usage_db is kr.db and st.runner.usage_strategy == sid
            assert st.runner.usage_market == "KR"


# ====================================================================== 장부 보기
def test_ledger_view_groups_by_month_and_day() -> None:
    db = _db()
    sol = _llm(OPENAI, "gpt-6.1-sol")
    local = _llm("http://gpu-box.local:8000/v1", "qwen@gpu-box.local", profile="free")
    u = {"prompt_tokens": 1_000, "completion_tokens": 100, "cached_tokens": 400}
    record_call(
        db,
        datetime(2026, 10, 20, 10, tzinfo=KST),
        kind="session",
        run_id=1,
        llm=sol,
        res=ChatResult("", usage=u),
        market="KR",
    )
    record_call(
        db,
        datetime(2026, 11, 3, 10, tzinfo=KST),
        kind="session",
        run_id=2,
        llm=sol,
        res=ChatResult("", usage=u),
        market="US",
    )
    record_call(
        db,
        datetime(2026, 11, 4, 9, tzinfo=KST),
        kind="session",
        run_id=3,
        llm=local,
        res=ChatResult("", usage={"prompt_tokens": 50, "completion_tokens": 5}),
        market="KR",
        strategy="lynch",
    )
    out = ledger_view(db, datetime(2026, 11, 4, 15, tzinfo=KST), months=3)
    assert out["months"] == ["2026-09", "2026-10", "2026-11"] and out["month"] == "2026-11"
    by = {(g["month"], g["market"], g["model"]): g for g in out["groups"]}
    one = (600 * 2 + 400 * 0.10 + 100 * 10) / 1e6
    assert by[("2026-10", "KR", "gpt-6.1-sol")]["cost"] == pytest.approx(one, abs=1e-6)
    us = by[("2026-11", "US", "gpt-6.1-sol")]
    assert (us["calls"], us["cache_read"], us["cache_known"], us["by_source"]) == (1, 400, 1, {"priced": 1})
    lab = by[("2026-11", "KR", "qwen")]  # 자체 서버 주소는 떼어 낸다
    assert (lab["profile"], lab["provider"], lab["strategy"], lab["cost"]) == ("free", "local", "lynch", 0.0)
    assert "gpu-box" not in json.dumps(out)
    assert sorted(d["day"] for d in out["daily"]) == ["2026-11-03", "2026-11-04"]
    oct_ = ledger_view(db, datetime(2026, 11, 4, 15, tzinfo=KST), months=3, month="2026-10")
    assert [d["day"] for d in oct_["daily"]] == ["2026-10-20"]


# ====================================================================== API·공식 대조
def test_costs_endpoint(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False) as client:
        r = client.get("/api/llm/costs?months=2")
        assert r.status_code == 200
        body = r.json()
        assert {"groups", "daily", "months", "month", "strategies", "usd_krw", "current_model"} <= set(body)
        assert body["strategies"]["lynch"]["name"] == "피터 린치"
        # 주요 모델 단가표 (비교용) — OpenAI·Google 만, Anthropic 없음
        assert {p["provider"] for p in body["prices"]} == {"openai", "google"}
        assert client.get("/api/llm/costs?month=2026-13").status_code == 422
        off = client.get("/api/llm/costs/official").json()
        status = {p["provider"]: p["status"] for p in off["providers"]}
        assert status == {"openai": "no_key", "openrouter": "no_key", "google": "unsupported"}


@respx.mock
async def test_official_report_reconciles_openai_and_openrouter() -> None:
    billing._cache.clear()
    db = _db()
    now = datetime(2026, 11, 4, 12, tzinfo=UTC)
    sol = _llm(OPENAI, "gpt-6.1-sol")
    record_call(
        db,
        now,
        kind="session",
        run_id=1,
        llm=sol,
        res=ChatResult("", usage={"prompt_tokens": 1_000, "completion_tokens": 100}),
    )
    orr = _llm(OPENROUTER, "deepseek/deepseek-v9")
    record_call(
        db,
        now,
        kind="session",
        run_id=1,
        llm=orr,
        res=ChatResult("", usage={"prompt_tokens": 10, "completion_tokens": 1}, cost=0.002),
    )
    respx.get(billing.OPENAI_USAGE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "start_time": 0,
                        "results": [
                            {
                                "model": "gpt-6.1-sol-2026-10-01",
                                "num_model_requests": 1,
                                "input_tokens": 1_000,
                                "input_cached_tokens": 0,
                                "output_tokens": 100,
                            }
                        ],
                    }
                ],
                "has_more": False,
            },
        )
    )
    respx.get(billing.OPENAI_COSTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "results": [
                            {
                                "amount": {"value": 0.002, "currency": "usd"},
                                "line_item": "gpt-6.1-sol, input",
                            },
                            {
                                "amount": {"value": 0.001, "currency": "usd"},
                                "line_item": "gpt-6.1-sol, output",
                            },
                            {
                                "amount": {"value": 0.5, "currency": "usd"},
                                "line_item": "Web search tool calls",
                            },
                        ]
                    }
                ],
                "has_more": False,
            },
        )
    )
    key_route = respx.get(billing.OR_KEY_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"usage": 9.0, "usage_daily": 0.002, "usage_weekly": 0.01, "usage_monthly": 0.05}},
        )
    )
    s = Settings(
        llm_base_url="",
        llm_model="deepseek/deepseek-v9",
        llm_api_key=SecretStr("sk-or-v1-testkey"),
        openai_admin_key=SecretStr("admin-testkey"),
    )
    out = await billing.official_report(db, s, "2026-11", now)
    by = {p["provider"]: p for p in out["providers"]}
    oa = by["openai"]
    assert oa["status"] == "ok" and oa["official_total"] == pytest.approx(0.503)
    row = next(r for r in oa["rows"] if r["model"] == "gpt-6.1-sol")
    assert row["official"]["requests"] == 1 and row["official"]["cost"] == pytest.approx(0.003)
    assert row["ledger"]["calls"] == 1 and row["ledger"]["cost"] == pytest.approx(
        (1_000 * 2 + 100 * 10) / 1e6
    )
    o = by["openrouter"]
    assert o["status"] == "ok"
    assert [(w["key"], w["official"]["cost"], w["ledger"]["cost"]) for w in o["windows"]] == [
        ("daily", 0.002, 0.002),
        ("weekly", 0.01, 0.002),
        ("monthly", 0.05, 0.002),
    ]
    assert key_route.calls.last.request.headers["Authorization"] == "Bearer sk-or-v1-testkey"
    text_ = json.dumps(out)
    assert "sk-or-v1-testkey" not in text_ and "admin-testkey" not in text_
    # 10분 안에는 다시 묻지 않는다
    await billing.official_report(db, s, "2026-11", now)
    assert key_route.call_count == 1
    billing._cache.clear()


@respx.mock
async def test_official_report_errors_never_leak_keys() -> None:
    billing._cache.clear()
    respx.get(billing.OPENAI_USAGE_URL).mock(return_value=httpx.Response(401, json={"error": "bad"}))
    s = Settings(llm_base_url="", llm_model="m", openai_admin_key=SecretStr("admin-secret-1"))
    out = await billing.official_report(_db(), s, "2026-11", datetime(2026, 11, 4, tzinfo=UTC))
    oa = next(p for p in out["providers"] if p["provider"] == "openai")
    assert oa["status"] == "error" and "401" in oa["note"] and "admin-secret-1" not in json.dumps(out)
    billing._cache.clear()


# ====================================================================== 예전 마이그레이션
def test_migration_backfills_old_session_totals(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    from app.core.config import BACKEND_DIR
    from app.main import _migrate

    db = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "app" / "db" / "migrations"))
    cfg.attributes["url"] = db
    command.upgrade(cfg, "0001")
    eng = create_engine(db)
    with eng.begin() as con:
        for rid, mode, usage in (
            (1, "llm-tools", {"prompt_tokens": 170000, "completion_tokens": 9000, "total_tokens": 179000}),
            (2, "rule", {}),
        ):
            con.execute(
                text(
                    "INSERT INTO agent_runs (id, trigger, mode, model, status, started_at, market_view, summary, usage)"
                    " VALUES (:id, 'schedule', :mode, 'q', 'DONE', :ts, '', '', :usage)"
                ),
                {
                    "id": rid,
                    "mode": mode,
                    "ts": datetime(2026, 11, 2, 1, tzinfo=UTC),
                    "usage": json.dumps(usage),
                },
            )
    _migrate(db)
    with eng.connect() as con:
        rows = con.execute(
            text("SELECT kind, run_id, input_tokens, output_tokens, source, cost_source FROM llm_usage")
        ).all()
    assert [tuple(r) for r in rows] == [("session", 1, 170000, 9000, "backfill", "")]  # 요금은 부팅 때 매긴다


def test_price_list_for_comparison() -> None:
    """화면 '주요 모델 단가' 표: 오늘 단가, OpenAI 캐시 쓰기(입력 1.25배), 장문 구간, 예정된 인상."""
    rows = {r["id"]: r for r in price_list(date(2026, 11, 6))}
    sol = rows["gpt-6.1-sol"]
    assert (sol["input"], sol["cached"], sol["output"], sol["cache_write"]) == (2, 0.10, 10, 2.5)
    assert rows["gpt-6-astra"]["long_from"] == 272_000 and rows["gpt-6-astra"]["long"]["input"] == 20
    argon = rows["gemini-4-argon"]
    assert argon["input"] == 2 and argon["cache_write"] is None  # Google 은 캐시 쓰기 요금 없음
    assert argon["next"] == {"since": "2026-12-01", "input": 4, "cached": 0.20, "output": 20}
    # 인상일이 지나면 새 단가가 오늘 단가, 예정 없음
    later = {r["id"]: r for r in price_list(date(2026, 12, 2))}["gemini-4-argon"]
    assert later["input"] == 4 and later["next"] is None
