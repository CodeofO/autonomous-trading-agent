"""REST API — 주제별 모듈(auth·system·orders·…)의 라우터를 하나의 `/api` 라우터로 묶는다 (B10).

경로·응답은 예전 단일 파일(routes.py)과 같다. `router` 는 국장(그리고 전역) API, `us_router()` 는 같은 핸들러를
/api/us 아래에 한 벌 더 건 미장 API 다. 다른 모듈은 예전처럼 `from app.api.routes import C, U` 로 의존성을 쓴다.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.api.routes import (
    agent,
    auth,
    chat,
    control,
    extended,
    lab,
    llm,
    market,
    markets,
    memory,
    newsroom,
    orders,
    push,
    reports,
    risk,
    schedule,
    system,
)
from app.api.routes.common import US_PREFIX, C, U, get_c, require_user

__all__ = ["C", "GLOBAL_PATHS", "US_PREFIX", "U", "get_c", "require_user", "router", "us_router"]

router = APIRouter(prefix="/api")
for _m in (
    auth,
    system,
    orders,
    control,
    push,
    agent,
    llm,
    schedule,
    extended,
    chat,
    risk,
    memory,
    market,
    reports,
    markets,
    newsroom,
    lab,
):
    router.include_router(_m.router)

# /api/us 아래에 두지 않는 전역 API (계정·알림·LLM 설정·비용·주요 지표·시장 목록·합산 요약·뉴스 룸)
GLOBAL_PATHS = (
    "/api/health",
    "/api/auth",
    "/api/push",
    "/api/llm",
    "/api/market/indicators",
    "/api/market/benchmarks",
    "/api/markets",
    "/api/overview",
    "/api/news-room",
)


def us_router() -> APIRouter:
    """미장 스택용 라우터: 시장별 API 를 /api/us/... 로 한 벌 더 건다. 핸들러는 같고 `get_c` 가 경로로 미장을 고른다."""
    r = APIRouter()
    r.include_router(llm.us_only)
    for route in router.routes:
        if not isinstance(route, APIRoute) or route.path.startswith(GLOBAL_PATHS):
            continue
        r.add_api_route(
            US_PREFIX + route.path.removeprefix("/api"),
            route.endpoint,
            methods=sorted(route.methods),
            status_code=route.status_code,
            response_model=route.response_model,
            name=f"us_{route.name}",
            summary=route.summary,
            description=route.description,
            tags=["미장"],
        )
    return r
