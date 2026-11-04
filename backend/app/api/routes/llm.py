"""LLM 설정·비용 API (D-41, D-43, D-45, D-46) 와 미장 전용 LLM 프로필 (/api/us)."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.agent.costs import summary
from app.agent.llm_check import check as llm_check
from app.agent.profiles import (
    API_CHOICES,
    MODEL_KEY,
    TEST_KEY,
    active_profile,
    build_llm,
    check_model,
    paid_model,
    test_endpoint,
)
from app.agent.profiles import configured as profile_configured
from app.agent.profiles import info as profile_info
from app.api.routes.common import US_PREFIX, C, U, _containers
from app.container import US_PROFILE_KEY, Container, market_llm, market_profile
from app.core.clock import KST
from app.core.http import Route, SafeHttpClient
from app.engine.events import SYSTEM

router = APIRouter()


@router.get("/llm/costs")
async def llm_costs(c: C, _: U, months: int = Query(12, ge=1, le=36)) -> dict[str, Any]:
    """토큰 사용량(월별)과 상용 모델로 환산한 예상 요금 (D-41)."""
    per_day = c.scheduler.sessions_per_day()  # 지금 일정(기본 또는 화면에서 정한 일정)의 하루 판단 수
    out = summary(c.db, c.clock.now(), c.cal, months, sessions_per_day=per_day)
    fx = next((i for i in (await c.indicators.snapshot())["items"] if i["key"] == "USDKRW"), None)
    out["usd_krw"] = fx["price"] if fx else None
    out["current_model"] = c.runner.llm.model if c.runner.llm else None
    out["profile"] = active_profile(c.kv, c.settings)
    return out


class LlmProfileBody(BaseModel):
    active: Literal["free", "paid"]


class LlmModelBody(BaseModel):
    model: str = Field(min_length=1, max_length=128)


def _stacks(request: Request) -> list[Container]:
    return list(_containers(request).values())


def _uses(c: Container, kr: Container, profile: str) -> bool:
    return market_profile(kr.kv, kr.settings, c.code) == profile


def _rebuild_llms(request: Request, kr: Container, profile: str) -> None:
    """무료/유료 묶음 설정이 바뀌면 그 묶음을 쓰는 모든 시장 스택의 LLM 을 다시 만든다 (D-48)."""
    for st in _stacks(request):
        if _uses(st, kr, profile):
            st.runner.llm = market_llm(kr.kv, kr.settings, st.code)


def _llm_busy_any(request: Request, kr: Container, profile: str | None = None) -> None:
    """그 묶음을 쓰는 스택 중 하나라도 판단 세션·답장 중이면 409 (한 세션 안에서 모델이 섞이지 않게)."""
    _llm_busy(kr)
    for st in _stacks(request):
        if st is not kr and (profile is None or _uses(st, kr, profile)):
            _llm_busy(st)


def _profile(c: Container, active: str | None = None) -> dict[str, Any]:
    today = c.clock.now().astimezone(KST).date()
    return profile_info(c.settings, active or active_profile(c.kv, c.settings), c.kv, today)


def _llm_busy(c: Container) -> None:
    p = c.runner.progress
    if p.get("active") or p.get("replying"):
        raise HTTPException(409, "판단 세션이나 답장이 진행 중이에요. 끝난 뒤에 바꿔 주세요")


@router.get("/llm/profile")
def llm_profile(c: C, _: U) -> dict[str, Any]:
    """무료(LLM_TEST_*)/유료(LLM_*) 중 지금 쓰는 LLM (D-43), 유료 제공사와 고를 수 있는 모델 (D-45)."""
    return _profile(c)


@router.put("/llm/model")
def set_llm_model(body: LlmModelBody, c: C, _: U, request: Request) -> dict[str, Any]:
    """유료 모델 바꾸기 (D-45). .env 키로 알아낸 제공사의 모델만 고를 수 있다."""
    s = c.settings
    if not profile_configured(s, "paid"):
        raise HTTPException(400, ".env 에 LLM_* 를 채우고 다시 시작해야 쓸 수 있어요")
    prev = paid_model(c.kv, s)
    if body.model == prev:
        return _profile(c)
    reason = check_model(s, body.model)
    if reason:
        raise HTTPException(400, reason)
    _llm_busy_any(request, c, "paid")
    c.kv.set(MODEL_KEY, {"model": body.model})
    active = active_profile(c.kv, s)
    _rebuild_llms(request, c, "paid")  # 국장·미장 중 유료를 쓰는 스택
    c.hub.audit("CONFIG", f"유료 LLM 모델 변경: {prev} → {body.model}", None, model=body.model, previous=prev)
    when = "다음 판단·답장부터 적용돼요" if active == "paid" else "유료로 바꾸면 이 모델을 써요"
    c.hub.chat(SYSTEM, "text", f"🔄 유료 LLM 모델을 {body.model}(으)로 바꿨어요. {when}.")
    return _profile(c, active)


@router.put("/llm/profile")
def set_llm_profile(body: LlmProfileBody, c: C, _: U) -> dict[str, Any]:
    prev = active_profile(c.kv, c.settings)
    if body.active == prev:
        return _profile(c, prev)
    if not profile_configured(c.settings, body.active, c.kv):
        env = "LLM_TEST_*" if body.active == "free" else "LLM_*"
        raise HTTPException(400, f".env 에 {env} 를 채우고 다시 시작해야 쓸 수 있어요")
    _llm_busy(c)
    llm = build_llm(c.settings, body.active, c.kv)
    c.runner.llm = llm
    c.kv.set("llm_profile", {"active": body.active})
    name = {"free": "무료", "paid": "유료"}
    model = llm.model if llm else "-"
    c.hub.audit(
        "CONFIG",
        f"LLM 전환: {name[prev]} → {name[body.active]} ({model})",
        None,
        profile=body.active,
        model=model,
    )
    c.hub.chat(
        SYSTEM,
        "text",
        f"🔄 LLM 을 {name[body.active]} 모델({model})로 바꿨어요. 다음 판단·답장부터 적용돼요.",
    )
    return _profile(c, body.active)


class LlmTestEndpointBody(BaseModel):
    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=200)
    api: Literal["auto", "chat", "responses"] = "auto"
    reasoning_effort: str = Field(default="", max_length=16)


def _check_base_url(url: str) -> str:
    u = urlsplit(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
        raise HTTPException(
            400, "주소는 http(s)://호스트[:포트]/경로 형식이어야 해요 (예: http://서버:8000/v1)"
        )
    return url.strip().rstrip("/")


def _apply_free(c: Container, request: Request | None = None) -> None:
    if request is not None:
        _rebuild_llms(request, c, "free")  # 국장·미장 중 무료를 쓰는 스택 (미장 기본은 무료)
    elif active_profile(c.kv, c.settings) == "free":
        c.runner.llm = build_llm(c.settings, "free", c.kv)


@router.get("/llm/test-endpoint")
def get_test_endpoint(c: C, _: U) -> dict[str, Any]:
    """테스트용(무료) LLM 설정 (D-46). 키는 .env 에만 있고, 있는지만 알려 준다."""
    return test_endpoint(c.settings, c.kv)


@router.put("/llm/test-endpoint")
def set_test_endpoint(body: LlmTestEndpointBody, c: C, _: U, request: Request) -> dict[str, Any]:
    if body.api not in API_CHOICES:
        raise HTTPException(400, "호출 방식은 auto·chat·responses 중 하나예요")
    _llm_busy_any(request, c, "free")
    value = {
        "base_url": _check_base_url(body.base_url),
        "model": body.model.strip(),
        "api": body.api,
        "reasoning_effort": body.reasoning_effort.strip(),
    }
    c.kv.set(TEST_KEY, value)
    _apply_free(c, request)
    c.hub.audit("CONFIG", f"테스트용 LLM 변경: {value['model']} ({value['api']})", None, model=value["model"])
    return test_endpoint(c.settings, c.kv)


@router.delete("/llm/test-endpoint")
def reset_test_endpoint(c: C, _: U, request: Request) -> dict[str, Any]:
    _llm_busy_any(request, c, "free")
    c.kv.set(TEST_KEY, {})
    _apply_free(c, request)
    c.hub.audit("CONFIG", "테스트용 LLM 을 .env 값으로 되돌림", None)
    return test_endpoint(c.settings, c.kv)


@router.post("/llm/test-endpoint/models")
async def list_test_models(c: C, _: U, base_url: str | None = None) -> dict[str, Any]:
    """테스트용 서버의 모델 목록 (OpenAI 호환 GET {base}/models). 무료 엔드포인트에만 보낸다."""
    ep = test_endpoint(c.settings, c.kv)
    base = _check_base_url(base_url or ep["base_url"] or "http://")
    key = c.settings.llm_test_api_key.get_secret_value().strip() if c.settings.llm_test_api_key else ""
    parts = urlsplit(base)
    http = SafeHttpClient(
        [Route((parts.hostname or "").lower(), "GET", re.compile(re.escape(parts.path + "/models")))],
        rate_per_sec=2,
        timeout=15,
        max_retries=0,
    )
    try:
        r = await http.request(
            "GET", f"{base}/models", headers={"Authorization": f"Bearer {key}"} if key else {}
        )
    except Exception as e:
        raise HTTPException(502, f"모델 목록을 못 받았어요: {type(e).__name__}") from e
    finally:
        await http.aclose()
    if r.status_code != 200:
        raise HTTPException(502, f"모델 목록을 못 받았어요 (HTTP {r.status_code})")
    try:
        ids = sorted({str(m.get("id")) for m in r.json().get("data", []) if m.get("id")})
    except (ValueError, AttributeError) as e:
        raise HTTPException(502, "모델 목록 형식이 OpenAI 호환이 아니에요") from e
    return {"models": ids}


@router.post("/llm/test-endpoint/check")
async def check_test_endpoint(c: C, _: U) -> dict[str, Any]:
    """연결 확인: 테스트용(무료) LLM 에만 짧은 요청 4개 (유료로는 보내지 않는다)."""
    client = build_llm(c.settings, "free", c.kv)
    if client is None:
        raise HTTPException(400, "테스트용 LLM 주소와 모델을 먼저 저장해 주세요")
    client.max_tokens = 2048
    results: list[dict[str, Any]] = []
    ok = await llm_check(client, results, quiet=True)
    return {"ok": ok, "model": client.model, "api": client.api, "steps": results}


# ---------------------------------------------------------------------- /api/us 전용
us_only = APIRouter(prefix=US_PREFIX)


@us_only.get("/llm/profile")
def us_llm_profile(c: C, _: U) -> dict[str, Any]:
    """미장이 쓰는 LLM (무료/유료). 저장 위치: 국장 kv `llm_profile_us`, 기본 무료."""
    kv = c.llm_kv or c.kv
    today = c.clock.now().astimezone(KST).date()
    return {**profile_info(c.settings, market_profile(kv, c.settings, "US"), kv, today), "market": "US"}


@us_only.put("/llm/profile")
def set_us_llm_profile(body: LlmProfileBody, c: C, _: U) -> dict[str, Any]:
    kv = c.llm_kv or c.kv
    prev = market_profile(kv, c.settings, "US")
    if body.active != prev:
        if not profile_configured(c.settings, body.active, kv):
            env = "LLM_TEST_*" if body.active == "free" else "LLM_*"
            raise HTTPException(400, f".env 에 {env} 를 채우고 다시 시작해야 쓸 수 있어요")
        _llm_busy(c)
        kv.set(US_PROFILE_KEY, {"active": body.active})
        c.runner.llm = market_llm(kv, c.settings, "US")
        name = {"free": "무료", "paid": "유료"}
        model = c.runner.llm.model if c.runner.llm else "-"
        c.hub.audit(
            "CONFIG",
            f"미장 LLM 전환: {name[prev]} → {name[body.active]} ({model})",
            None,
            profile=body.active,
        )
        c.hub.chat(SYSTEM, "text", f"🔄 미장 LLM 을 {name[body.active]} 모델({model})로 바꿨어요.")
    return us_llm_profile(c, _)
