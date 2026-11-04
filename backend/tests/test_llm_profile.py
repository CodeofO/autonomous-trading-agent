"""LLM 무료(LLM_TEST_*)/유료(LLM_*) 전환: 기본값, 저장·유지, 진행 중 차단, 미설정 거부 (실제 호출 없음)."""

from __future__ import annotations

from pathlib import Path

from app.agent.profiles import active_profile
from tests.test_api import _client

PAID = {"llm_base_url": "https://paid.test/v1", "llm_model": "paid-model", "llm_api_key": "sk-paid-123456"}
FREE = {
    "llm_test_base_url": "https://free.test/v1",
    "llm_test_model": "free-model",
    "llm_test_api_key": "sk-free-123456",
}


def test_switch_between_free_and_paid(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **PAID, **FREE) as c:
        app = c.app.state.container  # type: ignore[attr-defined]
        r = c.get("/api/llm/profile").json()
        assert r["active"] == "paid"  # 유료가 설정돼 있으면 처음엔 유료
        assert r["profiles"]["free"] == {
            "label": "무료",
            "env": "LLM_TEST_*",
            "configured": True,
            "model": "free-model",
            "api": "chat",
            "reasoning_effort": None,
        }
        assert (
            "free.test" not in c.get("/api/llm/profile").text and "sk-" not in c.get("/api/llm/profile").text
        )

        assert c.put("/api/llm/profile", json={"active": "free"}).json()["active"] == "free"
        assert app.runner.llm.model == "free-model" and app.runner.llm.base_url == "https://free.test/v1"
        assert c.get("/api/system").json()["llm"]["profile"] == "free"
        assert active_profile(app.kv, app.settings) == "free"  # 저장돼서 재시작해도 유지
        assert "무료 모델(free-model)" in c.get("/api/chat").text

        app.runner._run = {"active": True}  # 판단 중에는 못 바꾼다
        assert c.put("/api/llm/profile", json={"active": "paid"}).status_code == 409
        app.runner._run = {"active": False}
        assert c.put("/api/llm/profile", json={"active": "paid"}).json()["active"] == "paid"
        assert app.runner.llm.model == "paid-model"


def test_unconfigured_profile_is_rejected(tmp_path: Path) -> None:
    with _client(
        tmp_path, auth_enabled=False, llm_test_base_url="", llm_test_model="", llm_test_api_key="", **PAID
    ) as c:
        r = c.put("/api/llm/profile", json={"active": "free"})
        assert r.status_code == 400 and "LLM_TEST_*" in r.json()["detail"]
        assert c.get("/api/llm/profile").json()["active"] == "paid"


def test_only_free_configured_starts_on_free(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, llm_base_url="", llm_model="", llm_api_key="", **FREE) as c:
        assert c.get("/api/llm/profile").json()["active"] == "free"
        assert c.get("/api/system").json()["llm"]["model"] == "free-model"
