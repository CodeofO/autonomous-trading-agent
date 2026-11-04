"""설정 화면에서 테스트용(무료) LLM 지정 (D-46): 저장·출처·되돌리기, 키 없는 서버, 모델 목록, 연결 확인은 무료에만 (실제 호출 없음)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import respx

from app.agent.profiles import TEST_KEY
from tests.test_api import _client
from tests.test_llm_profile import FREE, PAID

GPU = "http://gpu.test:8000/v1"


def _chat(content: str | None = None, call: dict[str, Any] | None = None) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if call:
        msg["tool_calls"] = [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
            }
        ]
    return httpx.Response(
        200,
        json={
            "choices": [{"message": msg, "finish_reason": "tool_calls" if call else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_save_override_reset_and_busy(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **PAID, **FREE) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        ep = c.get("/api/llm/test-endpoint").json()
        assert (ep["base_url"], ep["model"], ep["source"]["model"], ep["has_key"]) == (
            "https://free.test/v1",
            "free-model",
            ".env",
            True,
        )
        assert "sk-free" not in c.get("/api/llm/test-endpoint").text  # 키 값은 내보내지 않는다

        c.put("/api/llm/profile", json={"active": "free"})
        body = {"base_url": GPU + "/", "model": "qwen-next", "api": "chat", "reasoning_effort": "high"}
        ep = c.put("/api/llm/test-endpoint", json=body).json()
        assert (ep["base_url"], ep["model"], ep["source"]["base_url"]) == (GPU, "qwen-next", "화면")
        assert app.kv.get(TEST_KEY)["model"] == "qwen-next"  # DB 에 남아 재시작해도 유지
        assert (app.runner.llm.base_url, app.runner.llm.model, app.runner.llm.reasoning_effort) == (
            GPU,
            "qwen-next",
            "high",
        )
        assert c.get("/api/llm/profile").json()["profiles"]["free"]["model"] == "qwen-next"

        for bad in ("ftp://x/v1", "gpu.test:8000", "http://x/v1?k=1"):
            assert c.put("/api/llm/test-endpoint", json={**body, "base_url": bad}).status_code == 400, bad

        app.runner._run = {"active": True}
        assert c.put("/api/llm/test-endpoint", json=body).status_code == 409
        app.runner._run = {"active": False}

        ep = c.delete("/api/llm/test-endpoint").json()
        assert (ep["model"], ep["source"]["model"]) == ("free-model", ".env")
        assert app.runner.llm.model == "free-model"


@respx.mock
async def test_keyless_server_and_model_list(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **PAID) as c:  # .env 에 LLM_TEST_* 없음
        assert c.get("/api/llm/test-endpoint").json()["configured"] is False
        c.put("/api/llm/test-endpoint", json={"base_url": GPU, "model": "m1", "api": "chat"})
        assert (
            c.get("/api/llm/profile").json()["profiles"]["free"]["configured"] is True
        )  # 키 없이도 사용 가능

        models = respx.get(f"{GPU}/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "m2"}, {"id": "m1"}]})
        )
        assert c.post("/api/llm/test-endpoint/models").json() == {"models": ["m1", "m2"]}
        assert "Authorization" not in models.calls[0].request.headers
        other = "http://other.test/v1"
        respx.get(f"{other}/models").mock(return_value=httpx.Response(401))
        r = c.post("/api/llm/test-endpoint/models", params={"base_url": other})
        assert r.status_code == 502 and "401" in r.json()["detail"]


@respx.mock
def test_check_only_hits_free_endpoint(tmp_path: Path) -> None:
    paid = respx.post("https://paid.test/v1/chat/completions").mock(return_value=_chat("no"))
    free = respx.post("https://free.test/v1/chat/completions").mock(
        side_effect=[
            _chat("파랑"),
            _chat(call={"name": "get_price", "args": {"symbol": "005930"}}),
            _chat(call={"name": "get_price", "args": {"symbol": "005930"}}),
            _chat("삼성전자는 70,000원이에요."),
        ]
    )
    with _client(tmp_path, auth_enabled=False, **PAID, **FREE) as c:
        assert c.get("/api/llm/profile").json()["active"] == "paid"  # 유료를 쓰는 중이어도
        r = c.post("/api/llm/test-endpoint/check").json()
    assert r["ok"] is True and r["model"] == "free-model"
    assert [s["label"] for s in r["steps"]] == [
        "일반 답",
        "도구 호출",
        "도구 강제 호출",
        "도구 결과 되돌려 받기",
    ]
    assert free.call_count == 4 and paid.call_count == 0
    assert free.calls[0].request.headers["Authorization"] == "Bearer sk-free-123456"
