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
from app.agent.profiles import MODEL_KEY, build_llm, paid_model, set_registered
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
        ("", "sk-ant-abc", None),  # Anthropic 은 쓰지 않는다 (D-66)
    ],
)
def test_detect_openrouter(base: str, key: str, want: str | None) -> None:
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
    assert [m.group_label for m in models] == ["DeepSeek", "OpenAI", "openrouter", "Qwen"]
    assert "anthropic/claude-sonnet-x" not in ids  # 쓰지 않는 제공사 (D-66)
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
def test_openrouter_models_are_registered_then_picked(tmp_path: Path) -> None:
    """OpenRouter 는 화면에서 검색해 '등록'한 모델만 고른다 (D-71). 가격표(OpenAI·Google)는 키가 없으면 못 고른다."""
    route = respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=DATA))
    with _client(tmp_path, auth_enabled=False, **OR) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        assert app.runner.llm.api == "chat"  # openrouter.ai → Chat Completions
        paid = _paid(c.get("/api/llm/profile").json())
        assert (paid["provider"], paid["provider_label"]) == ("openrouter", "OpenRouter")
        assert paid["providers"] == {"openai": False, "google": False, "openrouter": True}
        assert paid["openrouter_registered"] == ["openai/gpt-6.1-sol"]  # .env 모델은 늘 등록돼 있다
        o = {m["id"]: m for m in paid["models"]}
        assert o["openai/gpt-6.1-sol"]["selected"] and o["openai/gpt-6.1-sol"]["provider"] == "openrouter"
        assert o["gpt-6.1-terra"]["available"] is False and "OPENAI_API_KEY" in o["gpt-6.1-terra"]["reason"]
        assert "deepseek/deepseek-v9" not in o  # 등록 전

        # 등록 안 한 모델은 못 고른다
        res = c.put("/api/llm/model", json={"model": "deepseek/deepseek-v9", "provider": "openrouter"})
        assert res.status_code == 400 and "등록" in res.json()["detail"]

        # 검색 (목록을 처음 한 번 받아 온다)
        found = c.get("/api/llm/openrouter/catalog?q=deep").json()
        assert route.call_count == 1 and [m["id"] for m in found["items"]] == ["deepseek/deepseek-v9"]
        assert found["items"][0]["registered"] is False
        assert not any(
            m["id"].startswith("anthropic/") for m in c.get("/api/llm/openrouter/catalog").json()["items"]
        )

        # 등록 → 고르기
        r = c.post("/api/llm/openrouter/registered", json={"id": "deepseek/deepseek-v9"})
        assert r.status_code == 200
        assert _paid(r.json())["openrouter_registered"] == ["openai/gpt-6.1-sol", "deepseek/deepseek-v9"]
        for bad in ("meta/tools-only", "mistral/no-tools", "no/such-model", "anthropic/claude-sonnet-x"):
            assert c.post("/api/llm/openrouter/registered", json={"id": bad}).status_code == 400, bad
        r = c.put("/api/llm/model", json={"model": "deepseek/deepseek-v9", "provider": "openrouter"})
        assert r.status_code == 200 and _paid(r.json())["model"] == "deepseek/deepseek-v9"
        assert app.runner.llm.model == "deepseek/deepseek-v9" and app.runner.llm.provider == "openrouter"
        assert app.kv.get(MODEL_KEY) == {"model": "deepseek/deepseek-v9", "provider": "openrouter"}
        o = {m["id"]: m for m in _paid(r.json())["models"]}
        assert (o["deepseek/deepseek-v9"]["input"], o["deepseek/deepseek-v9"]["selected"]) == (0.27, True)

        # 지금 쓰는 모델·.env 모델은 등록을 뺄 수 없다
        assert c.delete("/api/llm/openrouter/registered?id=deepseek/deepseek-v9").status_code == 409
        assert c.delete("/api/llm/openrouter/registered?id=openai/gpt-6.1-sol").status_code == 400
        c.put("/api/llm/model", json={"model": "openai/gpt-6.1-sol", "provider": "openrouter"})
        r = c.delete("/api/llm/openrouter/registered?id=deepseek/deepseek-v9")
        assert r.status_code == 200 and _paid(r.json())["openrouter_registered"] == ["openai/gpt-6.1-sol"]
        assert "sk-or-v1-test" not in c.get("/api/llm/profile").text


@respx.mock
def test_both_keys_switch_provider_in_the_app(tmp_path: Path) -> None:
    """OPENAI(LLM_*) + OPENROUTER_API_KEY 를 함께 두고 화면에서 제공사를 오간다 (D-71)."""
    respx.get(MODELS_URL).mock(return_value=httpx.Response(200, json=DATA))
    both = {
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-6.1-sol",
        "llm_api_key": "sk-proj-test-123456",
        "openrouter_api_key": "sk-or-v1-other-654321",
    }
    with _client(tmp_path, auth_enabled=False, **both) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        paid = _paid(c.get("/api/llm/profile").json())
        assert paid["providers"] == {"openai": True, "google": False, "openrouter": True}
        assert app.runner.llm.provider == "openai" and app.runner.llm.api == "responses"
        assert (
            c.post("/api/llm/openrouter/registered", json={"id": "deepseek/deepseek-v9"}).status_code == 200
        )
        c.put("/api/llm/model", json={"model": "deepseek/deepseek-v9", "provider": "openrouter"})
        llm = app.runner.llm
        assert (llm.provider, llm.base_url, llm.api, llm.model) == (
            "openrouter",
            "https://openrouter.ai/api/v1",
            "chat",
            "deepseek/deepseek-v9",
        )
        assert llm._api_key == "sk-or-v1-other-654321"  # OpenRouter 키로 (OpenAI 키를 보내지 않는다)
        # 다시 OpenAI 가격표 모델로
        c.put("/api/llm/model", json={"model": "gpt-6.1-terra", "provider": "openai"})
        llm = app.runner.llm
        assert (llm.provider, llm.api, llm.model, llm._api_key) == (
            "openai",
            "responses",
            "gpt-6.1-terra",
            "sk-proj-test-123456",
        )
        assert "sk-or-v1-other" not in c.get("/api/llm/profile").text


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
        set_registered(app.kv, ["deepseek/deepseek-v9"])
        app.kv.set(MODEL_KEY, {"model": "deepseek/deepseek-v9", "provider": "openrouter"})
        # 재시작 직후(목록 없음): 등록할 때 확인한 저장 모델을 그대로 쓰고 화면에도 보인다
        assert paid_model(app.kv, app.settings) == "deepseek/deepseek-v9"
        o = {m["id"]: m["selected"] for m in _paid(c.get("/api/llm/profile").json())["models"]}
        assert o["deepseek/deepseek-v9"] is True and o["openai/gpt-6.1-sol"] is False

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


def test_model_saved_for_openai_is_not_sent_to_openrouter(tmp_path: Path) -> None:
    """OpenAI 직접 연결에서 고른 'gpt-6.1-sol' 이 저장된 채 OpenRouter 로 바꿔도, 목록을 받기 전에 그 이름으로 부르지 않는다."""
    with _client(tmp_path, auth_enabled=False, **OR) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        app.kv.set(MODEL_KEY, {"model": "gpt-6.1-sol"})
        assert paid_model(app.kv, app.settings) == app.settings.llm_model
