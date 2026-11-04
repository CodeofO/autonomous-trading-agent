"""여러 API 모듈이 같이 쓰는 의존성·도우미: 시장 스택 고르기(`get_c`/`C`), 로그인 확인(`U`), 시각 표기."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request

from app.api.auth import verify_token
from app.container import Container
from app.db.models import ChatMessage


def _since(c: Container) -> datetime | None:
    """현재 계좌(모의/실거래) 기준 시각 — 모드를 바꾸면 이전 모드 기록은 목록에서 빠진다(D-34)."""
    raw = c.kv.get("account_source").get("since")
    return datetime.fromisoformat(raw) if raw else None


_bg: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


US_PREFIX = "/api/us"


def get_c(request: Request) -> Container:
    """요청 경로로 시장 스택을 고른다: /api/us/... → 미장, 그 밖 → 국장 (D-48)."""
    path = str(request.scope.get("path", ""))
    if path == US_PREFIX or path.startswith(US_PREFIX + "/"):
        us: Container | None = (getattr(request.app.state, "containers", None) or {}).get("US")
        if us is None:
            raise HTTPException(404, "미장이 꺼져 있어요 (US_MARKET_ENABLED=false)")
        return us
    c: Container = request.app.state.container
    return c


def _containers(request: Request) -> dict[str, Container]:
    cs: dict[str, Container] | None = getattr(request.app.state, "containers", None)
    return cs or {"KR": request.app.state.container}


C = Annotated[Container, Depends(get_c)]


def require_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    c: Container = request.app.state.container
    if not c.settings.auth_enabled:
        return {"uid": 0, "u": "local"}
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "로그인이 필요합니다")
    data = verify_token(c.secret_key, authorization.split(" ", 1)[1].strip())
    if data is None:
        raise HTTPException(401, "세션이 만료됐거나 올바르지 않습니다")
    return data


U = Annotated[dict[str, Any], Depends(require_user)]


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def _paper_only(c: Container) -> bool:
    return bool(c.market and c.market.paper_only)


def chat_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "ts": _iso(m.ts),
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "run_id": m.run_id,
        "payload": m.payload,
    }
