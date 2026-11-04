"""OpenRouter 를 유료 LLM 제공사로: 판별, 모델 목록(도구 호출 지원만·가격 환산·회사 묶음), 캐시, 화면·모델 바꾸기,
대화 호출 주소 (실제 네트워크 없음 — respx)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.agent import openrouter
from app.agent.openrouter import MODELS_URL, ModelCache, parse, per_million
from app.agent.profiles import MODEL_KEY, build_llm, paid_model
from app.core.config import Settings
from app.core.llm_providers import detect, resolve_base_url
from tests.test_api import _client

OR = {
    "llm_base_url": "https://openrouter.ai/api/v1",
    "llm_model": "openai/gpt-6.1-sol",
    "llm_api_key": "sk-or-v1-test-123456",
}
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TOOLS = ["max_tokens", "temperature", "tools", "tool_choice"]


def _m(mid: str, name: str, prompt: str, completion: str, params: list[str], ctx: int = 128000) -> dict:
    return {
        "id": mid,
        "canonical_slug": mid,
        "name": name,
        "created": 1760000000,
        "context_length": ctx,
        "pricing": {"prompt": prompt, "completion": completion, "request": "0"},
        "supported_parameters": params,
    }


DATA: dict[str, Any] = {
    "data": [
        _m("openai/gpt-6.1-sol", "OpenAI: GPT-6.1 Sol", "0.0000025", "0.00001", TOOLS),
        _m("anthropic/claude-sonnet-x", "Anthropic: Claude Sonnet X", "0.000003", "0.000015", TOOLS, 200000),
        _m("deepseek/deepseek-v9", "DeepSeek: DeepSeek V9", "0.00000027", "0.0000011", TOOLS),
        _m("qwen/qwen9:free", "Qwen: Qwen9 (free)", "0", "0", TOOLS),
        _m("openrouter/auto", "Auto Router", "-1", "-1", TOOLS, 0),
        _m("meta/tools-only", "Meta: Tools Only", "0.000001", "0.000001", ["tools"]),  # tool_choice 없음
        _m("mistral/no-tools", "Mistral: No Tools", "0.000001", "0.000001", ["temperature"]),
    ]
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    openrouter.CACHE.clear()
    yield
    openrouter.CACHE.clear()


@pytest.mark.parametrize(
    ("base", "key", "want"),
    [
        ("https://openrouter.ai/api/v1", "x", "openrouter"),
        ("https://openrouter.ai/api/v1", "sk-ant-x", "openrouter"),  # 주소가 우선
        ("", "sk-or-v1-abc", "openrouter"),  # sk- 보다 먼저
        ("", "sk-openai-abc", "openai"),
        ("", "sk-ant-abc", "anthropic"),
    ],
)
def test_detect_openrouter(base: str, key: str, want: str) -> None:
    assert detect(base, key) == want


def test_empty_base_url_uses_openrouter_address() -> None:
    assert resolve_base_url("", "sk-or-v1-abc") == "https://openrouter.ai/api/v1"
    s = Settings(llm_base_url="", llm_model="openai/gpt-6.1-sol", llm_api_key="sk-or-v1-abc")
    assert s.has_llm and s.llm_paid_base_url == "https://openrouter.ai/api/v1"


def test_parse_keeps_tool_capable_models_and_converts_prices() -> None:
    models = parse(DATA)
    ids = [m.id for m in models]
    assert "meta/tools-only" not in ids and "mistral/no-tools" not in ids
    by = {m.id: m for m in models}
    sol = by["openai/gpt-6.1-sol"]
    assert (sol.name, sol.group, sol.group_label) == ("GPT-6.1 Sol", "openai", "OpenAI")
    assert (sol.input, sol.output, sol.context_length) == (2.5, 10.0, 128000)
    assert (by["deepseek/deepseek-v9"].input, by["deepseek/deepseek-v9"].output) == (0.27, 1.1)
    assert (by["qwen/qwen9:free"].input, by["qwen/qwen9:free"].output) == (0, 0)
    auto = by["openrouter/auto"]  # 가변 가격(-1)·이름에 회사 없음
    assert (auto.input, auto.output, auto.name, auto.group_label, auto.context_length) == (
        None,
        None,
        "Auto Router",
        "openrouter",
        None,
    )
    # 회사 이름 → 모델 이름 순
    assert [m.group_label for m in models] == ["Anthropic", "DeepSeek", "OpenAI", "openrouter", "Qwen"]
    assert parse({"oops": 1}) == [] and parse(None) == []
    assert per_million("abc") is None and per_million("0.000001") == 1.0


@respx.mock
async def test_fetch_and_cache() -> None:
    route = respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=DATA))
    cache = ModelCache()
    assert not cache.loaded and cache.stale
    assert await cache.refresh("sk-or-v1-key") is True
    req = route.calls.last.request
    assert req.url.params["supported_parameters"] == "tools"
    assert req.headers["Authorization"] == "Bearer sk-or-v1-key"
    assert cache.loaded and not cache.stale and cache.has("openai/gpt-6.1-sol")
    assert not cache.has("meta/tools-only")

    assert await cache.refresh("k") is False  # 6시간 안 → 다시 안 부름
    assert await cache.refresh("k", force=True) is False  # 30초 안 연타도 막는다
    assert route.call_count == 1

    cache.fetched_at = time.time() - openrouter.TTL_SEC - 1
    cache._tried -= openrouter.MIN_REFETCH_SEC + 1
    assert cache.stale and await cache.refresh("k") is True and route.call_count == 2


@respx.mock
async def test_fetch_errors_keep_previous_list() -> None:
    cache = ModelCache()
    respx.get(MODELS_URL).mock(return_value=httpx.Response(401, json={"error": {"code": 401}}))
    assert await cache.refresh("sk-or-v1-bad") is False
    assert not cache.loaded and cache.error and "401" in cache.error and "sk-or" not in cache.error

    cache.store(parse(DATA))
    respx.get(MODELS_URL).mock(side_effect=httpx.ConnectError("down"))
    cache.fetched_at = 0.0
    cache._tried = 0.0
    assert await cache.refresh("k") is False
    assert cache.loaded and cache.has("openai/gpt-6.1-sol") and "ConnectError" in (cache.error or "")


@respx.mock
async def test_models_endpoint_without_key_sends_no_auth_header() -> None:
    route = respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=DATA))
    await openrouter.fetch_models("")
    assert "Authorization" not in route.calls.last.request.headers


def _paid(r: dict[str, Any]) -> dict[str, Any]:
    paid: dict[str, Any] = r["profiles"]["paid"]
    return paid


@respx.mock
def test_profile_lists_openrouter_models_and_switches(tmp_path: Path) -> None:
    route = respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=DATA))
    with _client(tmp_path, auth_enabled=False, **OR) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        assert app.runner.llm.api == "chat"  # openrouter.ai → Chat Completions
        paid = _paid(c.get("/api/llm/profile").json())
        assert (paid["provider"], paid["provider_label"]) == ("openrouter", "OpenRouter")
        assert (paid["models_source"], paid["models_loaded"], paid["models_stale"]) == (
            "openrouter",
            False,
            True,
        )
        assert paid["models_fetched_at"] is None and paid["models_error"] is None
        assert [m["id"] for m in paid["models"]] == ["openai/gpt-6.1-sol"]  # 불러오기 전엔 .env 모델만
        env = paid["models"][0]
        assert env["provider"] == "openrouter" and env["group"] == "openai" and env["selected"]
        res = c.put("/api/llm/model", json={"model": "deepseek/deepseek-v9"})
        assert res.status_code == 400 and "새로고침" in res.json()["detail"]

        r = c.post("/api/llm/openrouter/models/refresh")
        assert r.status_code == 200 and route.call_count == 1
        paid = _paid(r.json())
        assert paid["models_loaded"] is True and paid["models_stale"] is False and paid["models_fetched_at"]
        o = {m["id"]: m for m in paid["models"]}
        assert set(o) == {
            "openai/gpt-6.1-sol",
            "anthropic/claude-sonnet-x",
            "deepseek/deepseek-v9",
            "qwen/qwen9:free",
            "openrouter/auto",
        }
        assert all(m["provider"] == "openrouter" and m["available"] for m in o.values())
        sol = o["openai/gpt-6.1-sol"]
        assert (sol["group"], sol["group_label"], sol["input"], sol["output"]) == (
            "openai",
            "OpenAI",
            2.5,
            10.0,
        )
        assert sol["selected"] and sol["note"] == ".env"
        assert o["qwen/qwen9:free"]["note"] == "무료"
        assert not any(m["id"] == "gpt-6.1-terra" for m in paid["models"])  # 직접 연결용 가격표는 뺀다
        assert "sk-or-v1-test" not in r.text and "sk-or-v1-test" not in c.get("/api/llm/profile").text

        r = c.put("/api/llm/model", json={"model": "anthropic/claude-sonnet-x"})
        assert r.status_code == 200 and _paid(r.json())["model"] == "anthropic/claude-sonnet-x"
        assert app.runner.llm.model == "anthropic/claude-sonnet-x"
        assert app.kv.get(MODEL_KEY) == {"model": "anthropic/claude-sonnet-x"}
        for bad in ("meta/tools-only", "mistral/no-tools", "no/such-model", "gpt-6.1-terra"):
            assert c.put("/api/llm/model", json={"model": bad}).status_code == 400, bad
        assert app.runner.llm.model == "anthropic/claude-sonnet-x"


def test_refresh_rejected_for_other_providers(tmp_path: Path) -> None:
    other = {"llm_base_url": "https://api.openai.com/v1", "llm_model": "gpt-6.1-sol", "llm_api_key": "sk-x"}
    with _client(tmp_path, auth_enabled=False, **other) as c:
        assert c.post("/api/llm/openrouter/models/refresh").status_code == 400
        paid = _paid(c.get("/api/llm/profile").json())
        assert (paid["models_source"], paid["models_loaded"]) == ("catalog", True)


def test_refresh_requires_login(tmp_path: Path) -> None:
    with _client(tmp_path, **OR) as c:
        assert c.post("/api/llm/openrouter/models/refresh").status_code == 401


@respx.mock
def test_saved_model_survives_restart_until_list_loads(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **OR) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        app.kv.set(MODEL_KEY, {"model": "deepseek/deepseek-v9"})
        # 재시작 직후(목록 없음): 고를 때 확인한 저장 모델을 그대로 쓰고 화면에도 보인다
        assert paid_model(app.kv, app.settings) == "deepseek/deepseek-v9"
        ids = [(m["id"], m["selected"]) for m in _paid(c.get("/api/llm/profile").json())["models"]]
        assert ids == [("openai/gpt-6.1-sol", False), ("deepseek/deepseek-v9", True)]

        # 목록을 불러 보니 그 모델이 빠졌으면 .env 모델로 돌아가고, 쓰던 LLM 도 맞춘다
        app.runner.llm = build_llm(app.settings, "paid", app.kv)
        assert app.runner.llm.model == "deepseek/deepseek-v9"
        gone = {"data": [d for d in DATA["data"] if d["id"] != "deepseek/deepseek-v9"]}
        respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=gone))
        paid = _paid(c.post("/api/llm/openrouter/models/refresh").json())
        assert paid["model"] == "openai/gpt-6.1-sol" and app.runner.llm.model == "openai/gpt-6.1-sol"


@respx.mock
async def test_chat_goes_to_openrouter_chat_completions() -> None:
    route = respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "파랑"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )
    )
    for base in ("https://openrouter.ai/api/v1", ""):  # 주소를 비워도 sk-or- 키면 같은 곳
        s = Settings(llm_base_url=base, llm_model="openai/gpt-6.1-sol", llm_api_key="sk-or-v1-abc")
        llm = build_llm(s, "paid")
        assert llm is not None and llm.api == "chat"
        res = await llm.chat([{"role": "user", "content": "하늘색?"}])
        assert res.content == "파랑"
        req = route.calls.last.request
        assert req.headers["Authorization"] == "Bearer sk-or-v1-abc"
        assert b'"model":"openai/gpt-6.1-sol"' in req.content.replace(b" ", b"")
    assert route.call_count == 2
