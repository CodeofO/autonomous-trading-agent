"""LLM 설정 보조 API — OpenRouter 모델 목록 새로고침.

유료 LLM 키가 OpenRouter 면 화면의 모델 목록은 OpenRouter 목록 API(GET https://openrouter.ai/api/v1/models,
https://openrouter.ai/docs/api/api-reference/models/get-models)에서 온다. 앱을 켤 때 미리 부르지 않고,
화면이 목록이 비었거나(models_loaded=false) 오래됐을 때(models_stale=true) 이 API 를 부른다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.agent import openrouter
from app.agent.profiles import active_profile, build_llm, paid_model, provider
from app.agent.profiles import configured as profile_configured
from app.agent.profiles import info as profile_info
from app.api.routes import C, U
from app.container import Container
from app.core.clock import KST

router = APIRouter(prefix="/api")


def _profile(c: Container) -> dict[str, Any]:
    today = c.clock.now().astimezone(KST).date()
    return profile_info(c.settings, active_profile(c.kv, c.settings), c.kv, today)


@router.post("/llm/openrouter/models/refresh")
async def refresh_openrouter_models(
    c: C, _: U, force: bool = Query(True, description="false 면 목록이 6시간 안이면 다시 부르지 않는다")
) -> dict[str, Any]:
    """OpenRouter 모델 목록을 다시 불러오고 GET /api/llm/profile 과 같은 모양으로 돌려준다."""
    s = c.settings
    if not profile_configured(s, "paid") or provider(s) != "openrouter":
        raise HTTPException(400, "유료 LLM 이 OpenRouter 일 때만 목록을 불러올 수 있어요")
    key = s.llm_api_key.get_secret_value() if s.llm_api_key else ""
    await openrouter.CACHE.refresh(key, force=force)
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
