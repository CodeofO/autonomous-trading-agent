"""LLM 무료/유료 전환 (D-43).

.env 의 두 묶음을 화면에서 골라 쓴다. 고른 값은 app_settings 'llm_profile' 에 남아 재시작해도 유지된다.
- free: LLM_TEST_* (테스트용·무료 엔드포인트, 예: 자체 서버)
- paid: LLM_*      (실서비스·과금 엔드포인트)
"""

from __future__ import annotations

from typing import Any, Literal

from app.agent.llm import LLMClient, resolve_api
from app.core.config import Settings
from app.engine.settings_store import SettingsStore

Profile = Literal["free", "paid"]
PROFILES: tuple[Profile, ...] = ("free", "paid")
KEY = "llm_profile"
LABEL = {"free": "무료", "paid": "유료"}


def configured(s: Settings, p: str) -> bool:
    return s.has_test_llm if p == "free" else s.has_llm


def _fields(s: Settings, p: str) -> tuple[str, str, str, str, str]:
    """(base_url, model, api_key, api, reasoning_effort)"""
    if p == "free":
        key = s.llm_test_api_key.get_secret_value() if s.llm_test_api_key else ""
        return (
            s.llm_test_base_url.strip(),
            s.llm_test_model.strip(),
            key,
            s.llm_test_api,
            s.llm_test_reasoning_effort,
        )
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    return s.llm_base_url, s.llm_model, key, s.llm_api, s.llm_reasoning_effort


def build_llm(s: Settings, p: str) -> LLMClient | None:
    if not configured(s, p):
        return None
    base, model, key, api, effort = _fields(s, p)
    return LLMClient(
        base,
        model,
        key,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout_sec,
        api=api,
        reasoning_effort=effort,
    )


def active_profile(kv: SettingsStore, s: Settings) -> Profile:
    """저장된 선택이 쓸 수 있으면 그것, 아니면 유료(설정돼 있으면) → 무료 순."""
    saved = kv.get(KEY).get("active")
    if saved in PROFILES and configured(s, saved):
        return saved
    if s.has_llm or not s.has_test_llm:
        return "paid"
    return "free"


def info(s: Settings, active: str) -> dict[str, Any]:
    """화면용. 주소·키는 내보내지 않고 모델 이름과 호출 방식만."""
    out: dict[str, Any] = {"active": active, "profiles": {}}
    for p in PROFILES:
        base, model, _, api, effort = _fields(s, p)
        ok = configured(s, p)
        out["profiles"][p] = {
            "label": LABEL[p],
            "env": "LLM_TEST_*" if p == "free" else "LLM_*",
            "configured": ok,
            "model": model if ok else None,
            "api": resolve_api(api, base) if ok else None,
            "reasoning_effort": effort or None,
        }
    return out
