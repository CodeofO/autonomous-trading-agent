"""구글 검색 도구 search_web (D-49): 키가 있을 때만 등록(국장·미장), 세션(run_id)당 한도, 채팅 칩 표시,
에이전트 구조 화면의 출처 준비 상태. 실제 호출 없음 (respx)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.agent.tools import openai_tools
from app.core.clock import KST
from app.core.config import Settings
from app.db.models import AgentRun, ChatMessage
from tests.helpers import make_container
from tests.test_api import _client

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
KEY = {"gemini_api_key": "gm-test-key-123"}
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=KST)
OK = httpx.Response(
    200,
    json={
        "candidates": [
            {
                "content": {"parts": [{"text": "- 11/04 — Reuters — 엔비디아 실적 기대"}]},
                "groundingMetadata": {
                    "webSearchQueries": ["NVDA stock news"],
                    "groundingChunks": [
                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/a", "title": "reuters.com"}},
                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/b", "title": "cnbc.com"}},
                    ],
                },
            }
        ]
    },
)


def _names(tools: list[dict[str, Any]]) -> list[str]:
    return [t["function"]["name"] for t in tools]


def test_tool_registered_only_with_key_for_both_markets(tmp_path: Path) -> None:
    for market in ("KR", "US"):
        assert "search_web" not in _names(openai_tools(toss=True, market=market))
        names = _names(openai_tools(toss=True, market=market, web_limit=3))
        assert "search_web" in names and names[-1] == "submit_decisions"
        spec = next(
            t for t in openai_tools(market=market, web_limit=3) if t["function"]["name"] == "search_web"
        )
        assert "세션당 최대 3회" in spec["function"]["description"]
        assert spec["function"]["parameters"]["properties"]["days"]["maximum"] == 7

    plain = make_container(tmp_path / "a", NOW)
    assert plain.research.sources["web"] == "없음" and plain.research.google is None
    assert "search_web" not in _names(plain.runner._tools())

    keyed = make_container(tmp_path / "b", NOW, google_search_max_per_session=2, **KEY)
    assert keyed.research.sources["web"] == "GOOGLE"
    assert "search_web" in _names(keyed.runner._tools())
    assert "최대 2회" in json.dumps(keyed.runner._tools(), ensure_ascii=False)
    s = Settings(_env_file=None, **KEY)  # type: ignore[call-arg]
    assert s.has_google_search and "gm-test-key-123" in s.secret_values()
    assert s.google_search_model == "gemini-3.5-flash-lite" and s.google_search_max_per_session == 3


@respx.mock
async def test_tool_call_uses_run_id_limit_and_hides_links_in_chat(tmp_path: Path) -> None:
    route = respx.post(URL).mock(return_value=OK)
    c = make_container(tmp_path, NOW, google_search_max_per_session=2, **KEY)
    with c.db.session() as s:
        run = AgentRun(trigger="manual", mode="llm-tools", model="m", status="RUNNING", started_at=NOW)
        s.add(run)
        s.flush()
        rid = run.id
    tb = c.runner.tb
    out = await tb.call("search_web", {"query": "NVDA earnings", "days": 30, "run_id": 999}, run_id=rid)
    assert out["count"] == 2 and out["answer"].startswith("- 11/04") and out["days"] == 7  # 1~7일로 자름
    assert out["sources"][0] == {"title": "reuters.com", "uri": "https://vertexaisearch.cloud.google.com/a"}
    assert out["remaining_this_session"] == 1  # LLM 이 준 run_id(999)는 무시하고 이 세션으로 센다
    body = json.loads(route.calls[0].request.content)
    assert "NVDA earnings" in body["contents"][0]["parts"][0]["text"]
    assert route.calls[0].request.headers["x-goog-api-key"] == "gm-test-key-123"

    # 판단 세션 경로: 도구 칩은 검색어·건수만 (제목·링크는 매니저에게만)
    res = await c.runner._tool(rid, "search_web", {"query": "엔비디아 실적"})
    assert res["remaining_this_session"] == 0
    with c.db.session() as s:
        chip = s.scalars(select(ChatMessage).where(ChatMessage.kind == "tool")).all()[-1]
    assert chip.content == "🔎 구글 검색: “엔비디아 실적”"
    assert chip.payload["source"] == "GOOGLE" and chip.payload["line"] == "결과 2건"
    assert "vertexaisearch" not in json.dumps(chip.payload) and "reuters" not in json.dumps(chip.payload)

    third = await tb.call("search_web", {"query": "AAPL"}, run_id=rid)
    assert "한도(2회)" in third["error"] and len(route.calls) == 2  # 한도를 넘으면 요청을 보내지 않는다
    other = await tb.call("search_web", {"query": "AAPL"}, run_id=rid + 1)  # 다음 세션은 따로 센다
    assert other["count"] == 2

    respx.post(URL).mock(return_value=httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}))
    err = await tb.call("search_web", {"query": "x"}, run_id=rid + 2)
    assert err == {"error": "구글 검색 HTTP 403 PERMISSION_DENIED"}
    assert (await tb.call("search_web", {"query": "  "}, run_id=rid + 2))["error"] == "검색어가 비었어요"


async def test_tool_without_key_returns_error(tmp_path: Path) -> None:
    c = make_container(tmp_path, NOW)
    out = await c.runner.tb.call("search_web", {"query": "NVDA"}, run_id=1)
    assert "GEMINI_API_KEY" in out["error"]


@respx.mock
async def test_us_stack_has_own_counter(tmp_path: Path) -> None:
    respx.post(URL).mock(return_value=OK)
    with _client(tmp_path, NOW, auth_enabled=False, google_search_max_per_session=1, **KEY) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        assert us.research.google is not None and us.research.google is not kr.research.google
        assert "search_web" in _names(us.runner._tools())
        assert (await kr.runner.tb.call("search_web", {"query": "a"}, run_id=1))["count"] == 2
        assert (await us.runner.tb.call("search_web", {"query": "a"}, run_id=1))[
            "count"
        ] == 2  # 세션 번호가 같아도 따로


def test_structure_shows_google_readiness(tmp_path: Path) -> None:
    with _client(tmp_path / "off", NOW, auth_enabled=False) as c:
        for prefix in ("/api", "/api/us"):
            st = c.get(f"{prefix}/agent/structure").json()
            src = {x["name"]: x for x in st["sources"]}
            assert src["GOOGLE"]["ready"] is False and src["GOOGLE"]["note"] == "GEMINI_API_KEY 없음 (유료)"
            tool = next(t for t in st["tools"] if t["name"] == "search_web")
            assert tool["ready"] is False and tool["source"] == "GOOGLE" and tool["group"] == "research"
        assert c.get("/api/system").json()["research"]["web"] == "없음"
    with _client(tmp_path / "on", NOW, auth_enabled=False, **KEY) as c:
        for prefix in ("/api", "/api/us"):
            st = c.get(f"{prefix}/agent/structure").json()
            src = {x["name"]: x for x in st["sources"]}
            assert src["GOOGLE"]["ready"] is True and src["GOOGLE"]["note"] is None
            assert next(t for t in st["tools"] if t["name"] == "search_web")["ready"] is True
