"""LLM 무료/유료 전환 (D-43)과 유료 모델 고르기 (D-45).

.env 의 두 묶음을 화면에서 골라 쓴다. 고른 값은 app_settings 'llm_profile' 에 남아 재시작해도 유지된다.
- free: LLM_TEST_* (테스트용·무료 엔드포인트, 예: 자체 서버)
- paid: LLM_*      (실서비스·과금 엔드포인트)

유료는 (제공사, 모델) 하나를 화면에서 고른다 (D-71). 제공사 키는 .env 에 함께 넣어 둔다:
OPENAI_API_KEY · OPENROUTER_API_KEY, 그리고 기본 연결 LLM_* (그 제공사 자리가 비었으면 LLM_* 키를 쓴다).
고른 값은 app_settings 'llm_paid_model' {model, provider} 에 남는다. 쓸 수 없게 되면 .env 의 LLM_* 로 돌아간다.

- OpenAI·Google: 앱 가격표(app/agent/costs.py) 모델
- OpenRouter: 화면에서 검색해 '등록'한 모델만 ('openrouter_models'). 검색은 OpenRouter 목록 API 캐시(도구 호출 지원만,
  app/agent/openrouter.py)에서. Anthropic 모델은 쓰지 않기로 해(D-66) 목록에서도 뺀다.
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
    prov, model = paid_choice(kv, s)
    return bool(model) and (prov in provider_keys(s) or (prov == provider(s) and s.has_llm))


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
    """LLM_* (기본 연결)의 제공사."""
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    return providers.detect(s.llm_base_url, key)


def _secret(v: Any) -> str:
    return v.get_secret_value().strip() if v is not None else ""


def provider_keys(s: Settings) -> dict[str, str]:
    """제공사별 쓸 수 있는 키 (D-71): OPENAI_API_KEY·OPENROUTER_API_KEY, 그리고 LLM_* 키(그 제공사 자리가 비었을 때)."""
    out: dict[str, str] = {}
    if _secret(s.openai_api_key):
        out["openai"] = _secret(s.openai_api_key)
    if _secret(s.openrouter_api_key):
        out["openrouter"] = _secret(s.openrouter_api_key)
    if _secret(s.qwen_api_key):
        out["qwen"] = _secret(s.qwen_api_key)
    prov, key = provider(s), _secret(s.llm_api_key)
    if prov in providers.SUPPORTED and key and s.llm_model:
        out.setdefault(prov, key)
    return out


def _base_url(s: Settings, prov: str | None) -> str:
    """제공사 공식 주소. LLM_* 가 그 제공사면 .env 주소를 그대로 (게이트웨이·지역 주소를 존중)."""
    if prov is None or prov == provider(s):
        return s.llm_paid_base_url
    if prov == "qwen":
        return providers.qwen_base_url(s.qwen_region)  # 키를 만든 지역 주소 (D-73)
    return providers.BASE_URL[prov]


# ---------------------------------------------------------------- OpenRouter 등록 모델 (D-71)
OR_MODELS_KEY = "openrouter_models"
OR_MAX = 30


def registered(kv: SettingsStore | None, s: Settings) -> list[str]:
    """화면에서 등록한 OpenRouter 모델 (등록한 순서). LLM_* 가 OpenRouter 면 .env 모델도 늘 들어 있다."""
    ids = [str(x) for x in (kv.get(OR_MODELS_KEY).get("ids") or [] if kv else []) if x]
    if provider(s) == "openrouter" and s.llm_model and s.llm_model not in ids:
        ids.insert(0, s.llm_model)
    return list(dict.fromkeys(ids))


def set_registered(kv: SettingsStore, ids: list[str]) -> None:
    kv.set(OR_MODELS_KEY, {"ids": list(dict.fromkeys(ids))[:OR_MAX]})


# ---------------------------------------------------------------- 유료 모델 = (제공사, 모델)
def _guess_provider(model: str, s: Settings) -> str | None:
    """예전 저장값(제공사 없이 모델만)의 제공사."""
    if model == s.llm_model:
        return provider(s)
    if "/" in model:
        return "openrouter"
    m = next((m for m in MODELS if m.id == model), None)
    return m.provider if m else None


def _usable(prov: str | None, model: str, s: Settings, kv: SettingsStore | None) -> tuple[bool, str | None]:
    """(쓸 수 있는지, 못 쓰면 이유)."""
    if prov == provider(s) and model == s.llm_model and s.has_llm:
        return True, None
    if prov is None:
        return False, "LLM_BASE_URL 이 자체 서버·게이트웨이라 .env 의 LLM_MODEL 만 써요"
    keys = provider_keys(s)
    if prov not in keys:
        env = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "qwen": "QWEN_API_KEY"}.get(
            prov, "LLM_API_KEY"
        )
        return False, f"{providers.LABEL.get(prov, prov)} 키가 .env 에 없어요 ({env})"
    if prov == "openrouter":
        if model not in registered(kv, s):
            return False, "등록하지 않은 OpenRouter 모델이에요"
        if openrouter.CACHE.loaded and not openrouter.CACHE.has(model):
            return False, "OpenRouter 목록에 없거나 도구 호출을 지원하지 않는 모델이에요"
        return True, None
    m = next((m for m in MODELS if m.id == model), None)
    if m is None or m.provider != prov:
        return False, "목록에 없는 모델이에요"
    return True, None


def paid_choice(kv: SettingsStore | None, s: Settings) -> tuple[str | None, str]:
    """유료로 쓸 (제공사, 모델): 화면에서 고른 값이 쓸 수 있으면 그것, 아니면 .env 의 LLM_* (없으면 (None, ""))."""
    saved = kv.get(MODEL_KEY) if kv else {}
    model = str(saved.get("model") or "")
    if model:
        prov = saved.get("provider") or _guess_provider(model, s)
        if _usable(prov, model, s, kv)[0]:
            return prov, model
    return (provider(s), s.llm_model) if s.has_llm else (None, "")


def paid_model(kv: SettingsStore | None, s: Settings) -> str:
    return paid_choice(kv, s)[1]


def check_model(
    s: Settings, model_id: str, prov: str | None = None, kv: SettingsStore | None = None
) -> str | None:
    """고를 수 없으면 이유, 고를 수 있으면 None. prov 를 안 주면 모델 이름으로 짐작한다(예전 화면)."""
    ok, reason = _usable(prov or _guess_provider(model_id, s), model_id, s, kv)
    return None if ok else reason


def _option(
    *,
    id: str,
    name: str,
    prov: str,
    tier: str,
    input: float | None,
    output: float | None,
    note: str,
    ok: bool,
    reason: str | None,
    selected: bool,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "provider": prov,
        "tier": tier,
        "input": input,
        "output": output,
        "note": note,
        "available": ok,
        "reason": reason,
        "selected": selected,
        **extra,
    }


def model_options(kv: SettingsStore | None, s: Settings, today: date) -> list[dict[str, Any]]:
    """고를 수 있는 유료 모델: OpenAI·Google 가격표 + 등록한 OpenRouter 모델 (+ 가격표에 없는 .env 모델)."""
    cur = paid_choice(kv, s)
    out: list[dict[str, Any]] = []
    env_prov = provider(s)
    if s.llm_model and env_prov not in ("openrouter",) and not any(m.id == s.llm_model for m in MODELS):
        out.append(
            _option(
                id=s.llm_model,
                name=f"{s.llm_model} (.env)",
                prov="env",
                tier="",
                input=None,
                output=None,
                note="",
                ok=s.has_llm,
                reason=None,
                selected=cur == (env_prov, s.llm_model),
            )
        )
    for m in MODELS:
        ok, reason = _usable(m.provider, m.id, s, kv)
        p = m.at(today)
        out.append(
            _option(
                id=m.id,
                name=m.name,
                prov=m.provider,
                tier=m.tier,
                input=p.input,
                output=p.output,
                note=m.note,
                ok=ok,
                reason=reason,
                selected=cur == (m.provider, m.id),
            )
        )
    cache = openrouter.CACHE
    for mid in registered(kv, s):
        info_ = cache.get(mid)
        ok, reason = _usable("openrouter", mid, s, kv)
        free = info_ is not None and info_.input == 0 and info_.output == 0
        note = ".env" if mid == s.llm_model and env_prov == "openrouter" else ""
        if free:
            note = f"{note} · 무료" if note else "무료"
        out.append(
            _option(
                id=mid,
                name=info_.name if info_ else mid,
                prov="openrouter",
                tier="",
                input=info_.input if info_ else None,
                output=info_.output if info_ else None,
                note=note,
                ok=ok,
                reason=reason,
                selected=cur == ("openrouter", mid),
                group="openrouter",
                group_label="OpenRouter",
                context_length=info_.context_length if info_ else None,
            )
        )
    return out


def openrouter_catalog(
    query: str, kv: SettingsStore | None, s: Settings, limit: int = 40
) -> list[dict[str, Any]]:
    """등록할 OpenRouter 모델 찾기 (도구 호출 지원 모델만, Anthropic 제외 — 목록 API 캐시에서)."""
    q = query.strip().lower()
    reg = set(registered(kv, s))
    out = []
    for m in openrouter.CACHE.models:
        if q and not any(q in v.lower() for v in (m.id, m.name, m.group, m.group_label)):
            continue
        out.append({**m.as_dict(), "registered": m.id in reg})
        if len(out) >= limit:
            break
    return out


def build_llm(s: Settings, p: str, kv: SettingsStore | None = None) -> LLMClient | None:
    if not configured(s, p, kv):
        return None
    base, model, key, api, effort = _fields(s, p, kv)
    if p == "paid":
        prov, model = paid_choice(kv, s)
        if prov is not None and prov != provider(s):  # LLM_* 와 다른 제공사: 그 제공사 키·공식 주소
            base, key, api = _base_url(s, prov), provider_keys(s)[prov], "auto"
        elif prov is not None:
            key = provider_keys(s).get(prov, key)
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
    if configured(s, "paid", kv) or not configured(s, "free", kv):
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
            prov, model = paid_choice(kv, s)
            if prov is not None and prov != provider(s):
                base, api = _base_url(s, prov), "auto"
        out["profiles"][p] = {
            "label": LABEL[p],
            "env": "LLM_TEST_*" if p == "free" else "LLM_*",
            "configured": ok,
            "model": model if ok else None,
            "api": resolve_api(api, base) if ok else None,
            "reasoning_effort": effort or None,
        }
    prov = paid_choice(kv, s)[0]
    keys = provider_keys(s)
    cache = openrouter.CACHE
    has_or = "openrouter" in keys
    out["profiles"]["paid"].update(
        provider=prov,
        provider_label=providers.LABEL.get(prov or ""),
        env_model=s.llm_model or None,
        # 제공사별 키가 있는지 (D-71) — 화면이 '키 있음/없음' 을 보여 준다. 키 값은 내보내지 않는다
        providers={k: k in keys for k in ("openai", "google", "openrouter", "qwen")},
        models=model_options(kv, s, today or date.today()),
        openrouter_registered=registered(kv, s),
        # OpenRouter 목록 API 캐시 (등록 모델 가격·검색용, 메모리 6시간)
        models_source="openrouter" if has_or else "catalog",
        models_loaded=cache.loaded if has_or else True,
        models_stale=cache.stale if has_or else False,
        models_fetched_at=(
            datetime.fromtimestamp(cache.fetched_at, UTC).isoformat() if has_or and cache.fetched_at else None
        ),
        models_error=cache.error if has_or else None,
    )
    return out
