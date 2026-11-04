"""트레이딩 룸 채팅·감사 로그 API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.routes.common import C, U, _iso, _spawn, chat_dict
from app.db.models import AuditEvent, ChatMessage

router = APIRouter()


@router.get("/chat")
def chat_history(
    c: C, _: U, before_id: int | None = None, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    q = select(ChatMessage).order_by(ChatMessage.id.desc()).limit(limit)
    if before_id:
        q = q.where(ChatMessage.id < before_id)
    with c.db.session() as s:
        return [chat_dict(m) for m in reversed(list(s.scalars(q)))]


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


@router.post("/chat", status_code=202)
async def chat_post(body: ChatBody, c: C, _: U) -> dict[str, Any]:
    _spawn(c.runner.answer(body.message.strip()))
    return {"accepted": True}


# ====================================================================== 감사 로그
@router.get("/events")
def events(
    c: C, _: U, type: str | None = None, limit: int = Query(200, ge=1, le=2000)
) -> list[dict[str, Any]]:
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if type:
        q = q.where(AuditEvent.type == type)
    with c.db.session() as s:
        return [
            {
                "id": e.id,
                "ts": _iso(e.ts),
                "type": e.type,
                "symbol": e.symbol,
                "message": e.message,
                "payload": e.payload,
            }
            for e in s.scalars(q)
        ]
