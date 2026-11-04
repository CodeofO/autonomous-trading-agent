"""LLM 무료/유료 전환 (D-43)과 유료 모델 고르기 (D-45).

.env 의 두 묶음을 화면에서 골라 쓴다. 고른 값은 app_settings 'llm_profile' 에 남아 재시작해도 유지된다.
- free: LLM_TEST_* (테스트용·무료 엔드포인트, 예: 자체 서버)
- paid: LLM_*      (실서비스·과금 엔드포인트)

유료는 .env 의 키·주소로 제공사(OpenAI·Google·Anthropic)를 알아내고, 그 제공사 모델 중 하나를 화면에서 고른다.
고른 모델은 app_settings 'llm_paid_model' 에 남는다. 제공사가 바뀌어 목록에 없으면 .env 의 LLM_MODEL 로 돌아간다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from app.agent.costs import MODELS
from app.agent.llm import LLMClient, resolve_api
from app.core import llm_providers as providers
from app.core.config import Settings
from app.engine.settings_store import SettingsStore

Profile = Literal["free", "paid"]
PROFILES: tuple[Profile, ...] = ("free", "paid")
KEY = "llm_profile"
MODEL_KEY = "llm_paid_model"
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
    return s.llm_paid_base_url, s.llm_model, key, s.llm_api, s.llm_reasoning_effort


def provider(s: Settings) -> str | None:
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    return providers.detect(s.llm_base_url, key)


def paid_model(kv: SettingsStore | None, s: Settings) -> str:
    """유료로 쓸 모델: 화면에서 고른 값이 지금 제공사에서 쓸 수 있으면 그것, 아니면 .env 의 LLM_MODEL."""
    saved = kv.get(MODEL_KEY).get("model") if kv else None
    if saved and saved != s.llm_model and _available(s, saved)[0]:
        return str(saved)
    return s.llm_model


def _available(s: Settings, model_id: str) -> tuple[bool, str | None]:
    if model_id == s.llm_model:
        return True, None
    m = next((m for m in MODELS if m.id == model_id), None)
    if m is None:
        return False, "목록에 없는 모델이에요"
    prov = provider(s)
    if m.provider in providers.UNSUPPORTED_REASON:
        return False, providers.UNSUPPORTED_REASON[m.provider]
    if prov is None:
        return False, "LLM_BASE_URL 이 자체 서버·게이트웨이라 .env 의 LLM_MODEL 만 써요"
    if m.provider != prov:
        return False, f"{providers.LABEL[m.provider]} 키가 .env 에 없어요"
    return True, None


def model_options(kv: SettingsStore | None, s: Settings, today: date) -> list[dict[str, Any]]:
    current = paid_model(kv, s)
    out: list[dict[str, Any]] = []
    if s.llm_model and not any(m.id == s.llm_model for m in MODELS):
        out.append(
            {
                "id": s.llm_model,
                "name": f"{s.llm_model} (.env)",
                "provider": "env",
                "tier": "",
                "input": None,
                "output": None,
                "note": "",
                "available": True,
                "reason": None,
                "selected": current == s.llm_model,
            }
        )
    for m in MODELS:
        ok, reason = _available(s, m.id)
        p = m.at(today)
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "provider": m.provider,
                "tier": m.tier,
                "input": p.input,
                "output": p.output,
                "note": m.note,
                "available": ok,
                "reason": reason,
                "selected": m.id == current,
            }
        )
    return out


def check_model(s: Settings, model_id: str) -> str | None:
    """고를 수 없으면 이유, 고를 수 있으면 None."""
    ok, reason = _available(s, model_id)
    return None if ok else reason


def build_llm(s: Settings, p: str, kv: SettingsStore | None = None) -> LLMClient | None:
    if not configured(s, p):
        return None
    base, model, key, api, effort = _fields(s, p)
    if p == "paid":
        model = paid_model(kv, s)
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


def info(
    s: Settings, active: str, kv: SettingsStore | None = None, today: date | None = None
) -> dict[str, Any]:
    """화면용. 주소·키는 내보내지 않고 모델 이름·호출 방식·제공사·고를 수 있는 모델만."""
    out: dict[str, Any] = {"active": active, "profiles": {}}
    for p in PROFILES:
        base, model, _, api, effort = _fields(s, p)
        ok = configured(s, p)
        if p == "paid":
            model = paid_model(kv, s)
        out["profiles"][p] = {
            "label": LABEL[p],
            "env": "LLM_TEST_*" if p == "free" else "LLM_*",
            "configured": ok,
            "model": model if ok else None,
            "api": resolve_api(api, base) if ok else None,
            "reasoning_effort": effort or None,
        }
    prov = provider(s)
    out["profiles"]["paid"].update(
        provider=prov,
        provider_label=providers.LABEL.get(prov or ""),
        env_model=s.llm_model or None,
        models=model_options(kv, s, today or date.today()) if configured(s, "paid") else [],
    )
    return out
