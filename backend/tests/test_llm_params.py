"""모델마다 다른 매개변수: 서버가 'Unsupported parameter' 로 거절하면 바꿔서 다시 보내고 기억한다 (실제 호출 없음)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from app.agent.llm import LLMClient, LLMError

URL = "https://llm.test/v1/chat/completions"
OK = {
    "choices": [{"message": {"role": "assistant", "content": "안녕"}, "finish_reason": "stop"}],
    "usage": {},
}


def _unsupported(param: str, code: str = "unsupported_parameter") -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "message": f"Unsupported: {param}",
                "type": "invalid_request_error",
                "param": param,
                "code": code,
            }
        },
    )


@respx.mock
async def test_switches_token_param_and_drops_temperature_then_remembers() -> None:
    sent: list[dict[str, Any]] = []
    replies = [
        _unsupported("max_tokens"),
        _unsupported("temperature", "unsupported_value"),
        httpx.Response(200, json=OK),
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(json.loads(req.content))
        return replies.pop(0) if replies else httpx.Response(200, json=OK)

    respx.post(URL).mock(side_effect=handler)
    c = LLMClient("https://llm.test/v1", "m", "sk-test-123456", max_tokens=1000)
    assert (await c.chat([{"role": "user", "content": "hi"}])).content == "안녕"
    assert [("max_tokens" in b, "max_completion_tokens" in b, "temperature" in b) for b in sent] == [
        (True, False, True),
        (False, True, True),
        (False, True, False),
    ]
    await c.chat([{"role": "user", "content": "또"}])  # 다음 호출은 처음부터 맞는 형식
    assert sent[-1].get("max_completion_tokens") == 1000 and "temperature" not in sent[-1] and len(sent) == 4


@respx.mock
async def test_other_400_errors_are_not_retried() -> None:
    route = respx.post(URL).mock(return_value=_unsupported("messages", "invalid_value"))
    c = LLMClient("https://llm.test/v1", "m", "sk-test-123456")
    try:
        await c.chat([{"role": "user", "content": "hi"}])
        raise AssertionError("LLMError 가 나야 함")
    except LLMError:
        pass
    assert route.call_count == 1
