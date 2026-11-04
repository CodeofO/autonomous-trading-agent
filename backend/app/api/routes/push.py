"""휴대폰 알림 API (D-39)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.common import C, U

router = APIRouter()


# ====================================================================== 휴대폰 알림 (D-39)
class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=128)


class PushSubscribeBody(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: PushKeys
    label: str = Field("", max_length=60)


class PushEndpointBody(BaseModel):
    endpoint: str | None = Field(None, max_length=1024)


class PushPrefs(BaseModel):
    fills: bool = True
    risk: bool = True
    alerts: bool = True
    agent: bool = False


@router.get("/push")
def push_state(c: C, _: U) -> dict[str, Any]:
    return c.push.state()


@router.post("/push/subscribe")
def push_subscribe(body: PushSubscribeBody, c: C, _: U) -> dict[str, Any]:
    if not c.push.available:
        raise HTTPException(409, c.push.reason or "휴대폰 알림을 쓸 수 없어요")
    try:
        did = c.push.subscribe(body.endpoint, body.keys.p256dh, body.keys.auth, body.label)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": did, "devices": len(c.push.state()["devices"])}


@router.post("/push/unsubscribe")
def push_unsubscribe(body: PushEndpointBody, c: C, _: U) -> dict[str, int]:
    return {"removed": c.push.unsubscribe(body.endpoint) if body.endpoint else 0}


@router.delete("/push/devices/{did}")
def push_remove_device(did: str, c: C, _: U) -> dict[str, int]:
    return {"removed": c.push.remove_device(did)}


@router.put("/push/prefs")
def push_prefs(body: PushPrefs, c: C, _: U) -> dict[str, bool]:
    return c.push.set_prefs(body.model_dump())


@router.post("/push/test")
async def push_test(body: PushEndpointBody, c: C, _: U) -> dict[str, int]:
    if not c.push.available:
        raise HTTPException(409, c.push.reason or "휴대폰 알림을 쓸 수 없어요")
    payload = {
        "title": "🔔 테스트 알림",
        "body": "휴대폰 알림이 잘 연결됐어요.",
        "url": "/settings",
        "tag": "test",
    }
    return await c.push.broadcast(payload, body.endpoint)
