"""전략 실험실 API (D-62): 주 계좌 전략 고르기, 전략 시나리오 켜고 끄기·비교·지금 판단·다시 시작.

시장마다 따로 (`/api/lab`, 미장 `/api/us/lab`). 시나리오는 언제나 모의계좌 — 실거래로 바꾸는 API 는 없다.
실거래는 주 계좌에서만, 사용자가 고른 전략 하나로.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.common import C, U
from app.container import Container
from app.engine.scenarios import LabError, ScenarioLab

router = APIRouter()


class MainBody(BaseModel):
    id: str = Field(min_length=1, max_length=32)


class ScenariosBody(BaseModel):
    enabled: list[str] = Field(max_length=16)


def _lab(c: Container) -> ScenarioLab:
    if c.lab is None:
        raise HTTPException(404, "전략 실험실이 없어요")
    return c.lab


@router.get("/lab")
async def get_lab(c: C, _: U) -> dict[str, Any]:
    """전략 목록, 주 계좌 전략, 켠 시나리오, 계좌별 비교(평가금·수익률·보유·체결·마지막 판단)."""
    return await _lab(c).view()


@router.put("/lab/main")
async def put_main(body: MainBody, c: C, _: U) -> dict[str, Any]:
    """주 계좌가 따를 전략. 실거래 계좌면 다음 판단부터 그 전략으로 실제 주문이 나간다 (사용자 판단)."""
    try:
        _lab(c).set_main(body.id)
    except LabError as e:
        raise HTTPException(400, str(e)) from e
    return await _lab(c).view()


@router.put("/lab/scenarios")
async def put_scenarios(body: ScenariosBody, c: C, _: U) -> dict[str, Any]:
    """켤 시나리오 전체 목록. 끈 시나리오의 기록은 남아 다시 켜면 이어서 돈다."""
    try:
        _lab(c).set_enabled(body.enabled)
    except LabError as e:
        raise HTTPException(400, str(e)) from e
    return await _lab(c).view()


@router.get("/lab/scenarios/{sid}")
async def get_scenario(sid: str, c: C, _: U) -> dict[str, Any]:
    try:
        return await _lab(c).detail(sid)
    except LabError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/lab/scenarios/{sid}/run", status_code=202)
async def run_scenario(sid: str, c: C, _: U) -> dict[str, Any]:
    """그 시나리오 하나를 지금 판단 (백그라운드). 다른 시나리오가 판단 중이면 409."""
    lab = _lab(c)
    try:
        lab.stack(sid)
    except LabError as e:
        raise HTTPException(404, str(e)) from e
    try:
        lab.start_one(sid)
    except LabError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": True, "id": sid}


@router.post("/lab/scenarios/{sid}/reset")
async def reset_scenario(sid: str, c: C, _: U) -> dict[str, Any]:
    """시나리오 계좌를 시작 금액으로 다시 시작 (기록은 남고 비교·기억은 지금부터)."""
    try:
        _lab(c).reset(sid)
    except LabError as e:
        raise HTTPException(409, str(e)) from e
    return await _lab(c).view()
