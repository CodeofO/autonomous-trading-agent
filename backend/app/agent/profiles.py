"""LLM 무료/유료 전환 (D-43)과 유료 모델 고르기 (D-45).

.env 의 두 묶음을 화면에서 골라 쓴다. 고른 값은 app_settings 'llm_profile' 에 남아 재시작해도 유지된다.
- free: LLM_TEST_* (테스트용·무료 엔드포인트, 예: 자체 서버)
- paid: LLM_*      (실서비스·과금 엔드포인트)

유료는 .env 의 키·주소로 제공사(OpenAI·Google·OpenRouter)를 알아내고, 그 제공사 모델 중 하나를 화면에서 고른다.
고른 모델은 app_settings 'llm_paid_model' 에 남는다. 제공사가 바뀌어 목록에 없으면 .env 의 LLM_MODEL 로 돌아간다.

OpenRouter 키면 정적 가격표 대신 OpenRouter 목록 API 로 불러온 모델(도구 호출 지원만, app/agent/openrouter.py)을
보여 준다. OpenRouter 목록에 OpenAI·Google 모델도 들어 있어, 직접 연결용 가격표 모델은 겹치지 않게 뺀다.
Anthropic 모델은 쓰지 않기로 해(D-66) OpenRouter 목록에서도 뺀다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from app.agent import openrouter
from app.agent.costs import MODELS
from app.agent.llm import LLMClient, resolve_api
from app.core import llm_providers as providers
from app.core.config import Settings
from app.engine.settings_store import SettingsStore

Profile = Literal["free", "paid"]
PROFILES: tuple[Profile, ...] = ("free", "paid")
KEY = "llm_profile"
MODEL_KEY = "llm_paid_model"
TEST_KEY = "llm_test_override"  # 화면에서 고친 테스트용(무료) LLM: base_url·model·api·reasoning_effort (D-46)
TEST_FIELDS = ("base_url", "model", "api", "reasoning_effort")
API_CHOICES = ("auto", "chat", "responses")
LABEL = {"free": "무료", "paid": "유료"}


def configured(s: Settings, p: str, kv: SettingsStore | None = None) -> bool:
    if p == "free":
        base, model, _, _, _ = _fields(s, p, kv)
        return bool(base and model)  # 키는 선택(키를 받지 않는 자체 서버도 있다)
    return s.has_llm


def test_override(kv: SettingsStore | None) -> dict[str, str]:
    raw = kv.get(TEST_KEY) if kv else {}
    return {k: str(raw.get(k) or "").strip() for k in TEST_FIELDS if raw.get(k)}


def test_endpoint(s: Settings, kv: SettingsStore | None) -> dict[str, Any]:
    """테스트용 LLM 설정 화면용: 지금 값과 출처(화면/.env). 키 값은 내보내지 않고 있는지만."""
    o = test_override(kv)
    env = {
        "base_url": s.llm_test_base_url.strip(),
        "model": s.llm_test_model.strip(),
        "api": s.llm_test_api,
        "reasoning_effort": s.llm_test_reasoning_effort.strip(),
    }
    return {
        **{k: o.get(k, env[k]) for k in TEST_FIELDS},
        "source": {k: ("화면" if k in o else ".env") for k in TEST_FIELDS},
        "env": env,
        "has_key": bool(s.llm_test_api_key and s.llm_test_api_key.get_secret_value().strip()),
        "configured": configured(s, "free", kv),
    }


def _fields(s: Settings, p: str, kv: SettingsStore | None = None) -> tuple[str, str, str, str, str]:
    """(base_url, model, api_key, api, reasoning_effort)"""
    if p == "free":
        key = s.llm_test_api_key.get_secret_value().strip() if s.llm_test_api_key else ""
        o = test_override(kv)
        return (
            o.get("base_url", s.llm_test_base_url.strip()).rstrip("/"),
            o.get("model", s.llm_test_model.strip()),
            key,
            o.get("api", s.llm_test_api),
            o.get("reasoning_effort", s.llm_test_reasoning_effort),
        )
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    return s.llm_paid_base_url, s.llm_model, key, s.llm_api, s.llm_reasoning_effort


def provider(s: Settings) -> str | None:
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    return providers.detect(s.llm_base_url, key)


def paid_model(kv: SettingsStore | None, s: Settings) -> str:
    """유료로 쓸 모델: 화면에서 고른 값이 지금 제공사에서 쓸 수 있으면 그것, 아니면 .env 의 LLM_MODEL."""
    saved = kv.get(MODEL_KEY).get("model") if kv else None
    if not saved or saved == s.llm_model:
        return s.llm_model
    if provider(s) == "openrouter" and not openrouter.CACHE.loaded:
        # 재시작 직후엔 목록이 비어 있다. 고를 때 목록으로 확인했으니 목록을 불러올 때까지는 그대로 쓴다.
        # 다만 '회사/모델' 꼴이 아니면 다른 제공사(OpenAI 직접 등)에서 고른 값이라 OpenRouter 에 보낼 수 없다
        return str(saved) if "/" in str(saved) else s.llm_model
    return str(saved) if _available(s, saved)[0] else s.llm_model


def _available(s: Settings, model_id: str) -> tuple[bool, str | None]:
    if model_id == s.llm_model:
        return True, None
    if provider(s) == "openrouter":
        if not openrouter.CACHE.loaded:
            return False, "OpenRouter 모델 목록을 아직 못 불러왔어요. 목록을 새로고침해 주세요"
        if openrouter.CACHE.has(model_id):
            return True, None
        return False, "OpenRouter 목록에 없거나 도구 호출을 지원하지 않는 모델이에요"
    m = next((m for m in MODELS if m.id == model_id), None)
    if m is None:
        return False, "목록에 없는 모델이에요"
    prov = provider(s)
    if prov is None:
        return False, "LLM_BASE_URL 이 자체 서버·게이트웨이라 .env 의 LLM_MODEL 만 써요"
    if m.provider != prov:
        return False, f"{providers.LABEL[m.provider]} 키가 .env 에 없어요"
    return True, None


def _openrouter_options(s: Settings, current: str) -> list[dict[str, Any]]:
    cache = openrouter.CACHE
    out: list[dict[str, Any]] = []
    extra = [x for x in dict.fromkeys((s.llm_model, current)) if x and x not in cache.ids]
    for mid in extra:  # .env 모델(또는 목록을 불러오기 전 저장된 모델)은 목록에 없어도 맨 앞에
        group = mid.split("/", 1)[0] if "/" in mid else "기타"
        out.append(
            {
                "id": mid,
                "name": f"{mid} (.env)" if mid == s.llm_model else mid,
                "provider": "openrouter",
                "group": group,
                "group_label": group,
                "tier": "",
                "input": None,
                "output": None,
                "context_length": None,
                "note": "",
                "available": True,
                "reason": None,
                "selected": mid == current,
            }
        )
    for m in cache.models:
        note = "무료" if m.input == 0 and m.output == 0 else ""
        if m.id == s.llm_model:
            note = ".env" + (f" · {note}" if note else "")
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "provider": "openrouter",
                "group": m.group,
                "group_label": m.group_label,
                "tier": "",
                "input": m.input,
                "output": m.output,
                "context_length": m.context_length,
                "note": note,
                "available": True,
                "reason": None,
                "selected": m.id == current,
            }
        )
    return out


def model_options(kv: SettingsStore | None, s: Settings, today: date) -> list[dict[str, Any]]:
    current = paid_model(kv, s)
    if provider(s) == "openrouter":
        return _openrouter_options(s, current)
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
    if not configured(s, p, kv):
        return None
    base, model, key, api, effort = _fields(s, p, kv)
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
        profile=p,
    )


def active_profile(kv: SettingsStore, s: Settings) -> Profile:
    """저장된 선택이 쓸 수 있으면 그것, 아니면 유료(설정돼 있으면) → 무료 순."""
    saved = kv.get(KEY).get("active")
    if saved in PROFILES and configured(s, saved, kv):
        return saved
    if s.has_llm or not configured(s, "free", kv):
        return "paid"
    return "free"


def info(
    s: Settings, active: str, kv: SettingsStore | None = None, today: date | None = None
) -> dict[str, Any]:
    """화면용. 주소·키는 내보내지 않고 모델 이름·호출 방식·제공사·고를 수 있는 모델만."""
    out: dict[str, Any] = {"active": active, "profiles": {}}
    for p in PROFILES:
        base, model, _, api, effort = _fields(s, p, kv)
        ok = configured(s, p, kv)
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
    cache = openrouter.CACHE
    is_or = prov == "openrouter"
    out["profiles"]["paid"].update(
        provider=prov,
        provider_label=providers.LABEL.get(prov or ""),
        env_model=s.llm_model or None,
        models=model_options(kv, s, today or date.today()) if configured(s, "paid") else [],
        # 모델 목록 출처: catalog(앱 가격표) | openrouter(OpenRouter 목록 API, 메모리 6시간)
        models_source="openrouter" if is_or else "catalog",
        models_loaded=cache.loaded if is_or else True,
        models_stale=cache.stale if is_or else False,  # True 면 화면이 새로고침을 불러 주면 좋다
        models_fetched_at=(
            datetime.fromtimestamp(cache.fetched_at, UTC).isoformat() if is_or and cache.fetched_at else None
        ),
        models_error=cache.error if is_or else None,
    )
    return out
