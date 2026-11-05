"""LLM 설정 보조 API — OpenRouter 모델 목록 새로고침·검색, 등록 모델 (D-45, D-71).

OpenRouter 키(OPENROUTER_API_KEY 또는 LLM_* 가 OpenRouter)가 있으면 목록 API(GET https://openrouter.ai/api/v1/models,
https://openrouter.ai/docs/api/api-reference/models/get-models)를 불러 메모리에 둔다(6시간). 화면은 그 목록에서
모델을 검색해 '등록'하고, 유료 모델 고르기에는 등록한 모델만 나온다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.agent import openrouter
from app.agent.profiles import (
    OR_MAX,
    active_profile,
    build_llm,
    openrouter_catalog,
    paid_choice,
    paid_model,
    provider_keys,
    registered,
    set_registered,
)
from app.agent.profiles import info as profile_info
from app.api.routes import C, U
from app.container import Container
from app.core.clock import KST

router = APIRouter(prefix="/api")


def _profile(c: Container) -> dict[str, Any]:
    today = c.clock.now().astimezone(KST).date()
    return profile_info(c.settings, active_profile(c.kv, c.settings), c.kv, today)


def _or_key(c: Container) -> str:
    key = provider_keys(c.settings).get("openrouter", "")
    if not key:
        raise HTTPException(400, "OpenRouter 키가 .env 에 없어요 (OPENROUTER_API_KEY)")
    return key


@router.post("/llm/openrouter/models/refresh")
async def refresh_openrouter_models(
    c: C, _: U, force: bool = Query(True, description="false 면 목록이 6시간 안이면 다시 부르지 않는다")
) -> dict[str, Any]:
    """OpenRouter 모델 목록을 다시 불러오고 GET /api/llm/profile 과 같은 모양으로 돌려준다."""
    s = c.settings
    await openrouter.CACHE.refresh(_or_key(c), force=force)
    # 재시작 직후 저장된 모델을 그대로 쓰다가, 목록을 보니 없어진 모델이면 .env 모델로 맞춘다(판단 중이 아닐 때만)
    p = c.runner.progress
    busy = bool(p.get("active") or p.get("replying"))
    if (
        not busy
        and active_profile(c.kv, s) == "paid"
        and c.runner.llm is not None
        and c.runner.llm.model != paid_model(c.kv, s)
    ):
        c.runner.llm = build_llm(s, "paid", c.kv)
    return _profile(c)


@router.get("/llm/openrouter/catalog")
async def openrouter_search(
    c: C, _: U, q: str = Query("", max_length=80), limit: int = Query(40, ge=1, le=100)
) -> dict[str, Any]:
    """등록할 OpenRouter 모델 검색 (도구 호출 지원만). 목록이 없거나 오래됐으면 먼저 불러온다."""
    await openrouter.CACHE.refresh(_or_key(c), force=False)
    return {
        "items": openrouter_catalog(q, c.kv, c.settings, limit),
        "total": len(openrouter.CACHE.models),
        "error": openrouter.CACHE.error,
    }


class RegisterBody(BaseModel):
    id: str = Field(min_length=3, max_length=128)


@router.post("/llm/openrouter/registered")
async def register_openrouter_model(body: RegisterBody, c: C, _: U) -> dict[str, Any]:
    """OpenRouter 모델 등록 — 유료 모델 고르기에 나온다. 목록 API 에 있는(도구 호출 지원) 모델만."""
    await openrouter.CACHE.refresh(_or_key(c), force=False)
    if not openrouter.CACHE.loaded:
        raise HTTPException(
            503, f"OpenRouter 목록을 받지 못했어요: {openrouter.CACHE.error or '잠시 뒤 다시'}"
        )
    if not openrouter.CACHE.has(body.id):
        raise HTTPException(400, "OpenRouter 목록에 없거나 도구 호출을 지원하지 않는 모델이에요")
    ids = registered(c.kv, c.settings)
    if body.id not in ids:
        if len(ids) >= OR_MAX:
            raise HTTPException(400, f"등록은 {OR_MAX}개까지예요. 안 쓰는 모델을 먼저 빼 주세요")
        set_registered(c.kv, [*ids, body.id])
        c.hub.audit("CONFIG", f"OpenRouter 모델 등록: {body.id}", None, model=body.id)
    return _profile(c)


@router.delete("/llm/openrouter/registered")
def unregister_openrouter_model(
    c: C, _: U, request: Request, id: str = Query(min_length=3, max_length=128)
) -> dict[str, Any]:
    """등록 빼기. 지금 쓰는 모델·.env 모델은 뺄 수 없다."""
    s = c.settings
    if paid_choice(c.kv, s) == ("openrouter", id):
        raise HTTPException(409, "지금 쓰는 모델이라 뺄 수 없어요. 다른 모델로 바꾼 뒤 빼 주세요")
    if id == s.llm_model:
        raise HTTPException(400, ".env 의 LLM_MODEL 이라 뺄 수 없어요")
    ids = [x for x in registered(c.kv, s) if x != id]
    set_registered(c.kv, [x for x in ids if x != s.llm_model])
    c.hub.audit("CONFIG", f"OpenRouter 모델 등록 해제: {id}", None, model=id)
    return _profile(c)
