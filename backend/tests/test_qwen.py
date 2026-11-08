"""Qwen Cloud 를 유료 LLM 제공사로 (D-73): 지역 주소, 키, 모델 고르기, 강제 도구 호출 때 생각 끄기, 단가 구간 (실제 호출 없음)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.agent.costs import CATALOG, price_list, price_tokens
from app.agent.llm import LLMClient
from app.core.llm_providers import detect, qwen_base_url
from tests.test_api import _client

SG = "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"


def test_region_urls_and_detection() -> None:
    assert qwen_base_url("ap-southeast-1") == SG
    assert qwen_base_url("us-east-1") == "https://us-east-1.maas.qwencloudapi.com/compatible-mode/v1"
    with pytest.raises(ValueError):
        qwen_base_url("mars-1")
    assert detect(SG, "") == "qwen"
    assert detect("https://maas.qwencloudapi.com/compatible-mode/v1", "") == "qwen"
    assert detect("https://evil-qwencloudapi.com/v1", "") is None


def test_tiered_prices() -> None:
    day = date(2026, 11, 9)
    flash = CATALOG["qwen3.7-flash"]
    assert price_tokens(flash, day, input=10_000, cache_read=0, output=0) == pytest.approx(
        10_000 * 0.03 / 1e6
    )
    assert price_tokens(flash, day, input=40_000, cache_read=0, output=0) == pytest.approx(
        40_000 * 0.10 / 1e6
    )
    assert price_tokens(flash, day, input=300_000, cache_read=0, output=0) == pytest.approx(
        300_000 * 0.20 / 1e6
    )
    # 캐시 적중은 자동 캐시 단가 (모델 페이지 'Input (Implicit Cache)')
    assert price_tokens(
        CATALOG["qwen3.8-flash"], day, input=5345, cache_read=3072, output=530
    ) == pytest.approx(((5345 - 3072) * 0.15 + 3072 * 0.016 + 530 * 0.47) / 1e6)
    assert price_tokens(flash, day, input=40_000, cache_read=30_000, output=0) == pytest.approx(
        (10_000 * 0.10 + 30_000 * 0.02) / 1e6
    )
    row = next(r for r in price_list(day) if r["id"] == "qwen3.7-flash")
    assert [t["from"] for t in row["tiers"]] == [32_000, 256_000] and row["cache_write"] is None


@respx.mock
def test_pick_qwen_model_with_key_and_region(tmp_path: Path) -> None:
    env = {
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-6.1-sol",
        "llm_api_key": "sk-proj-test-123456",
        "qwen_api_key": "sk-qwen-test-654321",
        "qwen_region": "us-east-1",
    }
    with _client(tmp_path, auth_enabled=False, **env) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        paid = c.get("/api/llm/profile").json()["profiles"]["paid"]
        assert paid["providers"]["qwen"] is True
        o = {m["id"]: m for m in paid["models"]}
        assert o["qwen3.8-flash"]["available"] and o["qwen3.8-flash"]["provider"] == "qwen"
        r = c.put("/api/llm/model", json={"model": "qwen3.8-flash", "provider": "qwen"})
        assert r.status_code == 200
        llm = app.runner.llm
        assert (llm.provider, llm.base_url, llm.api, llm.model, llm._api_key) == (
            "qwen",
            "https://us-east-1.maas.qwencloudapi.com/compatible-mode/v1",
            "chat",
            "qwen3.8-flash",
            "sk-qwen-test-654321",
        )
        assert "sk-qwen-test" not in c.get("/api/llm/profile").text


def test_qwen_unavailable_without_key(tmp_path: Path) -> None:
    env = {
        "llm_base_url": "https://api.openai.com/v1",
        "llm_model": "gpt-6.1-sol",
        "llm_api_key": "sk-proj-x1234567",
    }
    with _client(tmp_path, auth_enabled=False, **env) as c:
        o = {m["id"]: m for m in c.get("/api/llm/profile").json()["profiles"]["paid"]["models"]}
        assert o["qwen3.8-flash"]["available"] is False and "QWEN_API_KEY" in o["qwen3.8-flash"]["reason"]
        assert c.put("/api/llm/model", json={"model": "qwen3.8-flash", "provider": "qwen"}).status_code == 400


@respx.mock
async def test_forced_tool_call_turns_thinking_off_only_for_qwen() -> None:
    bodies: list[dict[str, Any]] = []

    def reply(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "qwen3.8-flash",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    respx.post(f"{SG}/chat/completions").mock(side_effect=reply)
    llm = LLMClient(SG, "qwen3.8-flash", "k", profile="paid")
    tools = [{"type": "function", "function": {"name": "submit", "parameters": {"type": "object"}}}]
    res = await llm.chat(
        [{"role": "user", "content": "hi"}], tools, {"type": "function", "function": {"name": "submit"}}
    )
    assert bodies[-1]["enable_thinking"] is False  # 특정 도구 강제 → 생각 끔
    await llm.chat([{"role": "user", "content": "hi"}], tools, None)
    assert "enable_thinking" not in bodies[-1]  # 평소에는 모델 기본값
    assert res.usage["cached_tokens"] == 40 and res.usage["reasoning_tokens"] == 5
