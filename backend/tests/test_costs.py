"""LLM 비용 예상: 호출별 사용량 기록, 캐시 적중 추정, 회사별 요금 환산, 월별 합계·이번 달 예상, 예전 세션 가져오기."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import create_engine, select, text

from app.agent.costs import MODELS, record_usage, row_cost, summary
from app.agent.llm import ChatResult
from app.core.clock import KST
from app.core.market_calendar import MarketCalendar
from app.db.models import LlmUsage
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

LLM_URL = "https://llm.test/v1/chat/completions"
D = date(2026, 11, 2)
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}
M = {m.id: m for m in MODELS}


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


def _rows(c: Any) -> list[LlmUsage]:
    with c.db.session() as s:
        return list(s.scalars(select(LlmUsage).order_by(LlmUsage.id)))


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
    rows = _rows(c)
    assert [(r.kind, r.run_id, r.input_tokens, r.cached_tokens, r.output_tokens) for r in rows] == [
        ("session", run_id, 1000, 0, 50),
        ("session", run_id, 1600, 1000, 40),  # 두 번째 호출 앞부분 = 첫 호출 입력
    ]
    assert rows[0].model == "test-model" and rows[0].source == "live"


async def test_chat_reply_is_recorded(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)

    async def fake_chat(*a: Any, **k: Any) -> ChatResult:
        return ChatResult(
            content="답", usage={"prompt_tokens": 3000, "completion_tokens": 120, "total_tokens": 3120}
        )

    c.runner.llm.chat = fake_chat
    await c.runner.answer("오늘 어때?")
    assert [(r.kind, r.input_tokens, r.output_tokens) for r in _rows(c)] == [("chat", 3000, 120)]


def test_prices_follow_each_providers_rules() -> None:
    day = date(2026, 11, 4)
    sess = {
        "kind": "session",
        "source": "live",
        "input_tokens": 1_000_000,
        "cached_tokens": 600_000,
        "output_tokens": 100_000,
    }
    # Anthropic Opus 5.5: 새 부분 40만 × 5분 캐시 쓰기 $5, 적중 60만 × $0.20, 출력 10만 × $20
    assert row_cost(M["claude-opus-5-5"], sess, day) == (6.0, 0.4 * 5 + 0.6 * 0.2 + 2.0)
    # OpenAI: 쓰기 요금 없이 적중분만 할인
    nocache, cache = row_cost(M["gpt-6.1-sol"], sess, day)
    assert nocache == 3.0 and round(cache, 6) == round(0.4 * 2 + 0.6 * 0.10 + 1.0, 6)
    # 단발 답장은 Anthropic 도 캐시 쓰기 요금 없이 기본 입력
    chat = {**sess, "kind": "chat", "cached_tokens": 0}
    assert row_cost(M["claude-opus-5-5"], chat, day) == (6.0, 6.0)
    # 장문 구간: Haiku 5.5 는 호출 1회 입력 10만 초과면 5배
    small = {**chat, "input_tokens": 1_000_000, "output_tokens": 0, "max_input_tokens": 90_000}
    assert row_cost(M["claude-haiku-5-5"], small, day)[0] == 0.10
    assert row_cost(M["claude-haiku-5-5"], {**small, "max_input_tokens": 120_000}, day)[0] == 0.50
    # 예정된 인상: Gemini 4 Argon 은 12월부터 2배
    assert row_cost(M["gemini-4-argon"], chat, date(2026, 11, 30))[0] == 3.0
    assert row_cost(M["gemini-4-argon"], chat, date(2026, 12, 1))[0] == 6.0


def test_monthly_summary_and_projection(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}
    record_usage(c.db, datetime(2026, 10, 15, 10, tzinfo=KST), "session", 1, "q", usage)
    record_usage(
        c.db, datetime(2026, 11, 2, 10, tzinfo=KST), "session", 2, "q", usage, cached_estimate=500_000
    )
    record_usage(c.db, datetime(2026, 11, 2, 11, tzinfo=KST), "chat", None, "q", usage)
    record_usage(
        c.db, datetime(2026, 11, 2, 12, tzinfo=KST), "chat", None, "q", {"prompt_tokens": 0}
    )  # 0 은 건너뜀

    now = datetime(2026, 11, 2, 15, tzinfo=KST)
    out = summary(c.db, now, MarketCalendar(), months=3)
    months = {m["month"]: m for m in out["months"]}
    assert list(months) == ["2026-09", "2026-10", "2026-11"]
    assert months["2026-11"] == {
        "month": "2026-11",
        "calls": 2,
        "input": 2_000_000,
        "cached": 500_000,
        "output": 200_000,
        "sessions": 1,
        "chats": 1,
        "backfill": 0,
    }
    sol = next(m for m in out["models"] if m["id"] == "gpt-6.1-sol")
    assert sol["months"]["2026-10"]["nocache"] == 3.0 and sol["months"]["2026-11"]["nocache"] == 6.0
    remaining = len(MarketCalendar().trading_days(date(2026, 11, 3), date(2026, 11, 30)))
    p = out["projection"]
    assert (
        p["remaining_trading_days"] == remaining and p["basis_days"] == 2
    )  # 최근 30일 중 쓴 날: 10/15, 11/2
    assert sol["daily_avg"]["nocache"] == 4.5  # (3 + 6) / 2일
    assert sol["projection"]["nocache"] == round(6.0 + 4.5 * remaining, 4)
    assert sol["projection"]["cache"] < sol["projection"]["nocache"]
    assert p["method"] == "history"

    # 일정 기준: 판단 간격을 바꿔도 예전 간격의 하루 사용량으로 부풀리지 않는다
    sched = summary(c.db, now, MarketCalendar(), months=3, sessions_per_day=3)
    sol = next(m for m in sched["models"] if m["id"] == "gpt-6.1-sol")
    assert sched["projection"]["method"] == "schedule" and sched["projection"]["basis_sessions"] == 2
    assert sol["per_session"]["nocache"] == 3.0
    assert sol["daily_avg"]["nocache"] == 3 * 3.0 + 3.0 / 2  # 세션 3번 + 답장 하루 평균


def test_migration_backfills_old_session_totals(tmp_path: Path) -> None:
    from app.main import _migrate

    db = f"sqlite:///{tmp_path / 'm.db'}"
    from alembic import command
    from alembic.config import Config

    from app.core.config import BACKEND_DIR

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
            text("SELECT kind, run_id, input_tokens, output_tokens, source FROM llm_usage")
        ).all()
    assert [tuple(r) for r in rows] == [("session", 1, 170000, 9000, "backfill")]  # 규칙 세션은 제외


def test_costs_endpoint(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False) as client:
        r = client.get("/api/llm/costs?months=2")
        assert r.status_code == 200
        body = r.json()
        assert len(body["months"]) == 2 and len(body["models"]) == 12
        assert {m["provider"] for m in body["models"]} == {"anthropic", "openai", "google"}
        assert body["usd_krw"] == 1380.5 and body["checked"] == "2026-11-04"
