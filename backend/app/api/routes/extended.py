"""장외 주문 설정 API (D-57): 프리마켓·애프터마켓(미장은 데이마켓도) 켜고 끄기. 시장마다 따로 (`/api/us/...`)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes.common import C, U, _is_live
from app.engine import extended_hours as ext
from app.engine.events import SYSTEM

router = APIRouter()


class ExtendedHoursBody(BaseModel):
    """바꿀 세션만 보낸다. 그 시장에 없는 세션(국장의 day_market 등)은 400."""

    day_market: bool | None = None
    pre_market: bool | None = None
    after_market: bool | None = None


@router.get("/extended-hours")
def get_extended_hours(c: C, _: U) -> dict[str, Any]:
    """장외 세션별 켬/끔과 다음 세션 시각 (토스 장 운영 캘린더 기준, 서머타임 반영)."""
    return ext.view(c.cal, c.clock.now(), market=c.code, live=_is_live(c))


@router.put("/extended-hours")
async def put_extended_hours(body: ExtendedHoursBody, c: C, _: U) -> dict[str, Any]:
    """바꾸면 바로 적용된다(재시작 없음). 켠 세션 안에서만 주문을 받는다."""
    changes = body.model_dump(exclude_none=True)
    before = ext.load(c.kv, c.cal)
    try:
        cfg = ext.save(c.kv, c.cal, changes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if cfg != before:
        text = ext.describe(cfg)
        live = " — 실거래 계좌라 장외에서도 실제 주문이 나가요" if _is_live(c) and any(cfg.values()) else ""
        c.hub.chat(SYSTEM, "text", f"🌙 장외 주문 설정을 바꿨어요: {text}{live}")
        c.hub.audit("CONFIG", f"장외 주문 설정: {text}", None, extended_hours=cfg, market=c.code)
    return ext.view(c.cal, c.clock.now(), market=c.code, live=_is_live(c))
