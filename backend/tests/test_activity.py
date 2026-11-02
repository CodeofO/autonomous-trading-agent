"""트레이딩 룸 활동 표시: 도구 칩 상세(출처·소요 시간·한 줄 요약), 진행 상황 복원, 답장 중복 방지."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.agent.activity import result_line, tool_source
from app.agent.llm import ChatResult
from app.db.models import ChatMessage
from app.engine.events import ANALYST
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

LLM_URL = "https://llm.test/v1/chat/completions"
D = date(2026, 11, 2)
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}


def _chat(c: Any) -> list[ChatMessage]:
    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage).order_by(ChatMessage.id)))


def _resp(content: str | None, calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


def _tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _drain(q: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@respx.mock
async def test_session_shows_tool_details_and_progress(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    q = c.hub.subscribe()
    respx.post(LLM_URL).mock(
        side_effect=[
            _resp("포트폴리오와 차트부터 볼게요", [_tc("get_portfolio", {}, 1)]),
            _resp(None, [_tc("get_price_history", {"symbol": "005930", "days": 60}, 2)]),
            _resp(None, [_tc("submit_decisions", {"summary": "관망", "decisions": []}, 3)]),
        ]
    )
    assert await c.runner.run("manual") is not None

    tools = [m for m in _chat(c) if m.kind == "tool"]
    assert [m.payload["tool"] for m in tools] == ["get_portfolio", "get_price_history"]
    pf, px = tools[0].payload, tools[1].payload
    assert pf["source"] == "모의계좌" and pf["ok"] is True and pf["step"] == 1
    assert pf["line"].startswith("평가금 ") and "보유 " in pf["line"]
    assert px["source"] == "토스증권" and px["step"] == 2 and isinstance(px["ms"], int)
    assert "원" in px["line"] and "RSI" in px["line"]

    progress = [e["data"] for e in _drain(q) if e["channel"] == "progress"]
    phases = [p.get("phase") for p in progress if p.get("active")]
    assert {"start", "thinking", "tool", "executing"} <= set(phases)
    mid = next(p for p in progress if p.get("phase") == "tool" and p.get("tool") == "get_price_history")
    assert mid["typing"] == mid["label"] and mid["source"] == "토스증권" and mid["tools"] == 1
    last = progress[-1]
    assert last["active"] is False and last["typing"] is None
    assert c.runner.progress["active"] is False and c.runner.progress["typing"] is None


def test_reply_finishing_restores_session_indicator(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.runner._run = {"active": True, "run_id": 1}
    c.runner._step("📰 뉴스 검색: “삼성전자”", phase="tool")
    c.runner._typing("reply", "답장 쓰는 중…")
    assert c.runner.progress["typing"] == "답장 쓰는 중…"
    c.runner._typing("reply", None)  # 답장이 끝나도 세션 표시는 남는다
    assert c.runner.progress["typing"] == "📰 뉴스 검색: “삼성전자”"


async def test_questions_sent_while_replying_get_one_combined_answer(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    calls: list[list[dict[str, Any]]] = []

    async def slow_chat(messages: list[dict[str, Any]], *a: Any, **k: Any) -> ChatResult:
        calls.append(messages)
        await asyncio.sleep(0.05)
        return ChatResult(content=f"답 {len(calls)}")

    c.runner.llm.chat = slow_chat
    # 답을 기다리다 새로고침하고 같은 질문을 두 번 더 보낸 상황
    await asyncio.gather(
        c.runner.answer("실현손익 얼마야?"),
        c.runner.answer("실현손익 얼마야??"),
        c.runner.answer("대답해줘"),
    )
    assert len(calls) == 2  # 첫 질문 + 밀린 질문 묶음 한 번 (질문마다 따로 답하지 않음)
    asked = [m["content"] for m in calls[1] if m["role"] == "user"]
    assert asked[-1] == "대답해줘" and "실현손익 얼마야??" in asked
    replies = [m for m in _chat(c) if m.role == ANALYST and m.payload.get("reply_to") == "user"]
    assert [m.content for m in replies] == ["답 1", "답 2"]
    assert c.runner.progress["replying"] is False

    await c.runner.resume_pending()  # 다 답했으면 재시작해도 다시 답하지 않음
    assert len(calls) == 2


async def test_failed_reply_is_not_retried_in_a_loop(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    attempts = 0

    async def broken(*a: Any, **k: Any) -> ChatResult:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("LLM down")

    c.runner.llm.chat = broken
    await c.runner.answer("안녕?")
    assert attempts == 1 and "답장 중 오류" in _chat(c)[-1].content


def test_progress_endpoint_and_result_lines(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False) as client:
        r = client.get("/api/agent/progress")
        assert r.status_code == 200 and r.json()["active"] is False and r.json()["replying"] is False

    assert tool_source("get_portfolio", live=True) == "토스 실계좌" and tool_source("search_news") == "NAVER"
    assert result_line("search_news", {"error": "HTTP 401"}) == "실패: HTTP 401"
    assert result_line("search_news", {"count": 2, "items": [{"title": "삼성전자 실적 발표"}]}) == (
        "뉴스 2건 · 「삼성전자 실적 발표」"
    )
    assert result_line("get_financials", {"available": False, "reason": "DART 고유번호 없음"}) == (
        "DART 고유번호 없음"
    )
    assert result_line(
        "get_supply_demand", {"investor_net_buy_sum_10d": {"foreigner": 1200, "institution": -300}}
    ) == ("외국인 +1,200주 · 기관 -300주")
