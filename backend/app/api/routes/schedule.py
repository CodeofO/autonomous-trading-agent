"""판단 일정 API (D-51)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.common import C, U
from app.container import Container
from app.core.clock import KST
from app.engine import decision_schedule as dsched
from app.engine.events import SYSTEM
from app.engine.scheduler import TZ_LABEL

router = APIRouter()


# ====================================================================== 판단 일정 (D-51)
class ScheduleBody(BaseModel):
    start: str = Field(max_length=8, description="HH:MM (시장 현지 시각, 5분 단위)")
    end: str = Field(max_length=8, description="HH:MM (시장 현지 시각, 5분 단위)")
    interval_minutes: int


@router.get("/schedule")
def get_schedule(c: C, _: U) -> dict[str, Any]:
    """판단 일정: 시작·끝·주기와 그날 판단 시각(시장 시각·한국 시각), 장 시간 밖 슬롯, 다음 실행."""
    return c.scheduler.view()


@router.put("/schedule")
async def put_schedule(body: ScheduleBody, c: C, _: U) -> dict[str, Any]:
    """바꾸면 바로 적용된다(재시작 없음). 지금 시각까지의 슬롯은 건너뛰고 다음 슬롯부터 돈다.

    async: 이벤트 루프에서 돌려 1분 디스패처(tick)와 실행 기록(kv sched_fired)을 번갈아 쓰지 않게 한다.
    """
    try:
        cfg = dsched.validate(body.start, body.end, body.interval_minutes)
    except dsched.ScheduleError as e:
        raise HTTPException(400, str(e)) from e
    prev = c.scheduler.config()
    dsched.save(c.kv, cfg)
    c.scheduler.skip_passed()
    view = c.scheduler.view()
    if prev != cfg:
        _announce_schedule(c, view, reset=False)
    return view


@router.delete("/schedule")
async def reset_schedule(c: C, _: U) -> dict[str, Any]:
    """기본 일정(.env)으로 되돌린다."""
    prev = c.scheduler.config()
    dsched.clear(c.kv)
    if prev is not None:
        c.scheduler.skip_passed()
    view = c.scheduler.view()
    if prev is not None:
        _announce_schedule(c, view, reset=True)
    return view


def _announce_schedule(c: Container, view: dict[str, Any], *, reset: bool) -> None:
    """감사 로그(CONFIG) + 트레이딩 룸 안내 한 줄 (시장별)."""
    us = c.code == "US"
    tz = c.cal.tz
    n = len(view["times"])
    span = f"{view['start']}~{view['end']}" + (f" ({view['tz_label']})" if us else "")
    every = f", {dsched.interval_label(view['interval_minutes'])}마다" if view["custom"] else ""
    nxt = c.scheduler.next_decision()
    when = ""
    if nxt is not None:
        local = nxt.astimezone(tz)
        day = "" if local.date() == c.cal.local_date(c.clock.now()) else f"{local:%m/%d} "
        when = f" — 다음 판단 {day}{local:%H:%M}" + (
            f" (한국 {nxt.astimezone(KST):%m/%d %H:%M})" if us else ""
        )
    head = "판단 일정을 기본값으로 되돌렸어요" if reset else "판단 일정을 바꿨어요"
    c.hub.chat(SYSTEM, "text", f"⏰ {head}: {span}{every} ({n}회){when}")
    c.hub.audit(
        "CONFIG",
        f"판단 일정 {'기본값으로' if reset else '변경'}: {view['start']}~{view['end']}, "
        f"{view['interval_minutes']}분 간격 ({n}회, {TZ_LABEL.get(c.code, str(tz))})",
        None,
        schedule=None if reset else {k: view[k] for k in ("start", "end", "interval_minutes")},
        times=view["times"],
        market=c.code,
    )
