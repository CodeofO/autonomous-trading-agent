"""엔진 제어 API: 킬 스위치·일시정지·재개."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.routes.common import C, U

router = APIRouter()


# ====================================================================== 엔진 제어
class KillBody(BaseModel):
    reason: str = Field("사용자 요청", max_length=200)


@router.post("/engine/kill")
async def kill(body: KillBody, c: C, _: U) -> dict[str, Any]:
    n = await c.engine.kill(body.reason)
    return {"status": c.engine.status, "canceled": n}


@router.post("/engine/resume")
def resume(c: C, _: U) -> dict[str, Any]:
    c.engine.resume()
    return {"status": c.engine.status}


@router.post("/engine/pause")
def pause(c: C, _: U) -> dict[str, Any]:
    c.engine.pause()
    return {"status": c.engine.status}
