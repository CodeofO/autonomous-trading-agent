"""유료 LLM 모델 고르기 (D-45): 제공사 판별, 모델 목록, 바꾸기·저장, 다른 제공사 거부 (실제 호출 없음, Anthropic 은 D-66 에서 뺌)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.agent.profiles import MODEL_KEY, build_llm, paid_model
from app.core.config import Settings
from app.core.llm_providers import detect, resolve_base_url
from tests.test_api import _client
from tests.test_llm_profile import FREE

OPENAI = {
    "llm_base_url": "https://api.openai.com/v1",
    "llm_model": "gpt-6.1-sol",
    "llm_api_key": "sk-test-123456",
}


@pytest.mark.parametrize(
    ("base", "key", "want"),
    [
        ("https://api.openai.com/v1", "x", "openai"),
        ("https://generativelanguage.googleapis.com/v1beta/openai", "x", "google"),
        ("https://api.anthropic.com/v1", "x", None),  # 쓰지 않는 제공사 (D-66)
        ("http://gpu-box.local:8000/v1", "sk-test", None),  # 주소가 있으면 주소가 우선(자체 서버)
        ("", "sk-ant-test", None),  # sk- 라고 OpenAI 로 보지 않는다
        ("", "sk-openai-test", "openai"),
        ("", "AIzaTest", "google"),
        ("", "AQ.Test", "google"),
        ("", "plain-key", None),
    ],
)
def test_detect_provider(base: str, key: str, want: str | None) -> None:
    assert detect(base, key) == want


def test_empty_base_url_uses_official_address() -> None:
    assert resolve_base_url("", "AIzaTest") == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert resolve_base_url("", "sk-ant-test") == ""  # Anthropic 은 쓰지 않는다 → 주소 없음
    s = Settings(llm_base_url="", llm_model="gemini-3.8-flash", llm_api_key="AIzaTest")
    assert s.has_llm and s.llm_paid_base_url.startswith("https://generativelanguage")
    assert not Settings(llm_base_url="", llm_model="m", llm_api_key="plain-key").has_llm


def _opts(r: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in r["profiles"]["paid"]["models"]}


def test_lists_models_and_switches_within_provider(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **OPENAI, **FREE) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        r = c.get("/api/llm/profile").json()
        paid = r["profiles"]["paid"]
        assert (paid["provider"], paid["provider_label"], paid["env_model"]) == (
            "openai",
            "OpenAI",
            "gpt-6.1-sol",
        )
        o = _opts(r)
        assert len(o) == 8 and o["gpt-6.1-sol"]["selected"] and o["gpt-6.1-sol"]["available"]
        assert not any(m["provider"] == "anthropic" for m in o.values())
        assert (o["gpt-6.1-terra"]["input"], o["gpt-6.1-terra"]["output"]) == (1, 5)
        assert o["gemini-4-argon"]["available"] is False and "Google 키" in o["gemini-4-argon"]["reason"]
        assert "sk-test" not in c.get("/api/llm/profile").text

        r = c.put("/api/llm/model", json={"model": "gpt-6.1-terra"}).json()
        assert r["profiles"]["paid"]["model"] == "gpt-6.1-terra" and _opts(r)["gpt-6.1-terra"]["selected"]
        assert app.runner.llm.model == "gpt-6.1-terra" and app.runner.llm.api == "responses"
        assert c.get("/api/system").json()["llm"]["model"] == "gpt-6.1-terra"
        assert app.kv.get(MODEL_KEY) == {"model": "gpt-6.1-terra"}  # 재시작해도 유지
        assert build_llm(app.settings, "paid", app.kv).model == "gpt-6.1-terra"  # type: ignore[union-attr]
        assert "gpt-6.1-terra" in c.get("/api/chat").text

        for bad in ("gemini-4-argon", "claude-opus-5-5", "no-such-model"):
            res = c.put("/api/llm/model", json={"model": bad})
            assert res.status_code == 400, bad
        assert app.runner.llm.model == "gpt-6.1-terra"

        app.runner._run = {"active": True}  # 판단 중에는 못 바꾼다
        assert c.put("/api/llm/model", json={"model": "gpt-6-luna"}).status_code == 409
        app.runner._run = {"active": False}

        # 무료를 쓰는 중에 바꾸면 저장만 하고, 유료로 돌아갈 때 그 모델을 쓴다
        c.put("/api/llm/profile", json={"active": "free"})
        c.put("/api/llm/model", json={"model": "gpt-6-luna"})
        assert app.runner.llm.model == "free-model"
        c.put("/api/llm/profile", json={"active": "paid"})
        assert app.runner.llm.model == "gpt-6-luna"

        # .env 모델로 되돌리기
        c.put("/api/llm/model", json={"model": "gpt-6.1-sol"})
        assert app.runner.llm.model == "gpt-6.1-sol"


def test_saved_model_from_another_provider_falls_back_to_env(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **OPENAI) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        app.kv.set(MODEL_KEY, {"model": "gemini-4-argon"})  # .env 를 Google 에서 OpenAI 로 바꾼 뒤
        assert paid_model(app.kv, app.settings) == "gpt-6.1-sol"


def test_custom_endpoint_offers_only_env_model(tmp_path: Path) -> None:
    custom = {
        "llm_base_url": "https://paid.test/v1",
        "llm_model": "paid-model",
        "llm_api_key": "sk-paid-123456",
    }
    with _client(tmp_path, auth_enabled=False, **custom) as c:
        paid = c.get("/api/llm/profile").json()["profiles"]["paid"]
        assert paid["provider"] is None and paid["provider_label"] is None
        env = paid["models"][0]
        assert (env["id"], env["provider"], env["available"], env["selected"]) == (
            "paid-model",
            "env",
            True,
            True,
        )
        assert not any(m["available"] for m in paid["models"][1:])
        sol = next(m for m in paid["models"] if m["id"] == "gpt-6.1-sol")
        assert "자체 서버" in sol["reason"]
        assert c.put("/api/llm/model", json={"model": "gpt-6.1-sol"}).status_code == 400
