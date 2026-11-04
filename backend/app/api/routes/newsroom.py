"""뉴스 룸 API (D-56) — 시장과 관계없는 공용 화면이라 /api 에만 있다(/api/us 아래에는 없음).

- GET  /api/news-room            최신 판·갱신 시각·다음 갱신 시각·자동 갱신 여부·요약 설정
- POST /api/news-room/refresh    수동 새로고침 시작(백그라운드). 화면은 refreshing 이 꺼질 때까지 GET 으로 기다린다
- PUT  /api/news-room/settings   LLM 요약 켜기/끄기
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.routes.common import U
from app.container import Container
from app.research.newsroom import NewsRoom, NewsRoomBusyError, NewsRoomUnavailableError

router = APIRouter()


def _kr(request: Request) -> tuple[Container, NewsRoom]:
    c: Container = request.app.state.container  # 국장 스택 (뉴스 룸은 국장 DB·kv·LLM 설정을 쓴다)
    if c.newsroom is None:
        raise HTTPException(404, "뉴스 룸이 없어요")
    return c, c.newsroom


def _view(c: Container, room: NewsRoom) -> dict[str, Any]:
    return room.view(auto=c.settings.enable_scheduler)


@router.get("/news-room")
async def news_room(request: Request, _: U) -> dict[str, Any]:
    c, room = _kr(request)
    return _view(c, room)


@router.post("/news-room/refresh", status_code=202)
async def refresh_news_room(request: Request, _: U) -> dict[str, Any]:
    c, room = _kr(request)
    try:
        room.start_manual()
    except NewsRoomUnavailableError as e:
        raise HTTPException(400, str(e)) from e
    except NewsRoomBusyError as e:
        raise HTTPException(409, str(e)) from e
    return _view(c, room)


class NewsRoomSettingsBody(BaseModel):
    summary: bool


@router.put("/news-room/settings")
async def news_room_settings(body: NewsRoomSettingsBody, request: Request, _: U) -> dict[str, Any]:
    c, room = _kr(request)
    room.set_summary(body.summary)
    return _view(c, room)
