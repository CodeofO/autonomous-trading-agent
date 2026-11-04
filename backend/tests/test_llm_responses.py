"""OpenAI Responses API 경로: 요청 형식, 추론 항목 되돌려 보내기, 사용량, 판단 세션 (실제 호출 없음)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.agent.llm import LLMClient, LLMError, llm_routes, resolve_api
from tests.fake_toss import at
from tests.helpers import make_container

URL = "https://api.openai.com/v1/responses"
D = date(2026, 11, 2)
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc-abc"}


def _resp(output: list[dict[str, Any]], status: str = "completed", **extra: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": status,
            "output": output,
            "usage": {
                "input_tokens": 1200,
                "input_tokens_details": {"cached_tokens": 800},
                "output_tokens": 300,
                "output_tokens_details": {"reasoning_tokens": 250},
                "total_tokens": 1500,
            },
            **extra,
        },
    )


def _call(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc_{i}",
        "call_id": f"call_{i}",
        "name": name,
        "arguments": json.dumps(args),
    }


def _text(t: str) -> dict[str, Any]:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": t}]}


def test_api_choice_and_routes() -> None:
    assert resolve_api("auto", "https://api.openai.com/v1") == "responses"
    assert resolve_api("auto", "https://generativelanguage.googleapis.com/v1beta/openai") == "chat"
    assert resolve_api("chat", "https://api.openai.com/v1") == "chat"
    paths = [r.path.pattern for r in llm_routes("https://api.openai.com/v1")]
    assert any("responses" in p for p in paths) and any("chat" in p for p in paths)


@respx.mock
async def test_request_shape_and_parsing() -> None:
    sent: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return _resp([REASONING, _call("get_price", {"symbol": "005930"}, 1)])

    respx.post(URL).mock(side_effect=handler)
    c = LLMClient(
        "https://api.openai.com/v1", "gpt-x", "sk-test-123456", max_tokens=4000, reasoning_effort="high"
    )
    tools = [
        {
            "type": "function",
            "function": {"name": "get_price", "description": "현재가", "parameters": {"type": "object"}},
        }
    ]
    res = await c.chat(
        [{"role": "system", "content": "규칙"}, {"role": "user", "content": "삼성전자?"}],
        tools,
        {"type": "function", "function": {"name": "get_price"}},
    )
    body = sent[0]
    assert (
        body["store"] is False
        and body["max_output_tokens"] == 4000
        and body["reasoning"] == {"effort": "high"}
    )
    assert "max_tokens" not in body and "messages" not in body
    assert body["input"] == [{"role": "system", "content": "규칙"}, {"role": "user", "content": "삼성전자?"}]
    assert body["tools"] == [
        {"type": "function", "name": "get_price", "description": "현재가", "parameters": {"type": "object"}}
    ]
    assert body["tool_choice"] == {"type": "function", "name": "get_price"}  # 평평한 형식
    assert [(t.id, t.name, t.arguments) for t in res.tool_calls] == [
        ("call_1", "get_price", {"symbol": "005930"})
    ]
    assert res.finish_reason == "tool_calls" and res.raw_output[0] == REASONING
    assert res.usage == {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "cached_tokens": 800,
        "reasoning_tokens": 250,
    }


@respx.mock
async def test_incomplete_and_temperature_adaptation() -> None:
    sent: list[dict[str, Any]] = []
    replies = [
        httpx.Response(
            400, json={"error": {"param": "temperature", "code": "unsupported_parameter", "message": "no"}}
        ),
        _resp([], status="incomplete", incomplete_details={"reason": "max_output_tokens"}),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return replies.pop(0)

    respx.post(URL).mock(side_effect=handler)
    c = LLMClient("https://api.openai.com/v1", "gpt-x", "sk-test-123456")
    res = await c.chat([{"role": "user", "content": "hi"}])
    assert ["temperature" in b for b in sent] == [True, False]
    assert res.finish_reason == "length" and res.content == ""


@respx.mock
async def test_real_responses_error_shape_drops_temperature_and_effort() -> None:
    """Responses API 의 실제 거절 형식: code 는 null, 메시지에만 'Unsupported parameter' (2026-11-04 세션 #72)."""
    sent: list[dict[str, Any]] = []
    replies = [
        httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'temperature' is not supported with this model.",
                    "type": "invalid_request_error",
                    "param": "temperature",
                    "code": None,
                }
            },
        ),
        httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported value: 'xhigh' is not supported with this model.",
                    "type": "invalid_request_error",
                    "param": "reasoning.effort",
                    "code": None,
                }
            },
        ),
        _resp([{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]),
        _resp([{"type": "message", "content": [{"type": "output_text", "text": "again"}]}]),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return replies.pop(0)

    respx.post(URL).mock(side_effect=handler)
    c = LLMClient("https://api.openai.com/v1", "gpt-x", "sk-test-123456", reasoning_effort="xhigh")
    assert (await c.chat([{"role": "user", "content": "hi"}])).content == "ok"
    assert [("temperature" in b, "reasoning" in b) for b in sent] == [
        (True, True),
        (False, True),
        (False, False),
    ]
    await c.chat([{"role": "user", "content": "hi"}])  # 기억해서 다음 호출부터 바로 뺀다
    assert len(sent) == 4 and "temperature" not in sent[3] and "reasoning" not in sent[3]


@respx.mock
async def test_other_invalid_request_is_not_retried() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"message": "Invalid 'input': empty.", "param": "input", "code": None}},
        )
    )
    c = LLMClient("https://api.openai.com/v1", "gpt-x", "sk-test-123456")
    with pytest.raises(LLMError, match="400"):
        await c.chat([{"role": "user", "content": "hi"}])
    assert route.call_count == 1


@respx.mock
async def test_agent_session_passes_reasoning_items_back(tmp_path: Path) -> None:
    c = make_container(
        tmp_path,
        at(D, 10),
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-x",
        llm_api_key="sk-test-123456",
    )
    assert c.runner.llm is not None and c.runner.llm.api == "responses"
    sent: list[dict[str, Any]] = []
    replies = [
        _resp([REASONING, _text("포트폴리오부터 볼게요"), _call("get_portfolio", {}, 1)]),
        _resp([_call("submit_decisions", {"summary": "관망", "decisions": []}, 2)]),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return replies.pop(0)

    respx.post(URL).mock(side_effect=handler)
    assert await c.runner.run("manual") is not None
    second = sent[1]["input"]
    i = second.index(REASONING)  # 첫 응답의 추론·도구 호출 항목을 그대로 되돌려 보냄
    assert second[i + 2] == _call("get_portfolio", {}, 1)
    out = second[i + 3]
    assert out["type"] == "function_call_output" and out["call_id"] == "call_1" and "equity" in out["output"]
    assert not any("_raw_output" in json.dumps(x) for x in second)


def test_llm_check_uses_test_endpoint_unless_live(monkeypatch: Any, capsys: Any) -> None:
    from app.agent import llm_check
    from app.core.config import Settings

    s = Settings(
        llm_test_base_url="",
        llm_test_model="",
        llm_test_api_key="",
        llm_base_url="https://llm.test/v1",
        llm_model="m",
        llm_api_key="sk-live-123456",
    )
    monkeypatch.setattr(llm_check, "get_settings", lambda: s)
    assert llm_check.main([]) == 2  # 테스트용이 비어 있으면 실서비스로 넘어가지 않는다
    assert "LLM_TEST_BASE_URL" in capsys.readouterr().out
    t = Settings(
        llm_test_base_url="https://test.llm/v1", llm_test_model="t", llm_test_api_key="sk-test-123456"
    )
    c = llm_check.client(t, live=False)
    assert c is not None and c.base_url == "https://test.llm/v1" and c.model == "t"
