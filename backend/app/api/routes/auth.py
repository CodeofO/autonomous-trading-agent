"""인증 API: 로컬 계정 만들기·로그인."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.auth import hash_password, issue_token, verify_password
from app.api.routes.common import C, U
from app.db.models import User

router = APIRouter()
# 이 서버 프로세스가 뜰 때마다 새로 정하는 값 — 재배포(재시작)되면 바뀌어 열려 있는 화면이 스스로 새로고침한다
BOOT_ID = secrets.token_hex(6)


# ====================================================================== 인증
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


@router.get("/health")
def health(c: C) -> dict[str, str]:
    """로그인 없이 부르는 상태 확인. boot: 서버 재시작마다 바뀌는 값 (화면 자동 새로고침, frontend/src/version.ts)."""
    return {"status": "ok", "trading_mode": c.settings.trading_mode, "boot": BOOT_ID}


@router.get("/auth/status")
def auth_status(c: C) -> dict[str, Any]:
    with c.db.session() as s:
        n = s.scalar(select(func.count()).select_from(User)) or 0
    return {"auth_enabled": c.settings.auth_enabled, "setup_required": c.settings.auth_enabled and n == 0}


@router.post("/auth/setup")
def auth_setup(body: Credentials, c: C) -> dict[str, Any]:
    with c.db.session() as s:
        if (s.scalar(select(func.count()).select_from(User)) or 0) > 0:
            raise HTTPException(409, "이미 계정이 있습니다")
        u = User(username=body.username, password_hash=hash_password(body.password))
        s.add(u)
        s.flush()
        uid = u.id
    c.hub.audit("AUTH", "로컬 계정 생성", None, username=body.username)
    token, exp = issue_token(c.secret_key, uid, body.username, c.settings.session_ttl_hours)
    return {"token": token, "expires_at": exp, "username": body.username}


@router.post("/auth/login")
def auth_login(body: Credentials, c: C) -> dict[str, Any]:
    with c.db.session() as s:
        u = s.scalar(select(User).where(User.username == body.username))
        ok = u is not None and verify_password(body.password, u.password_hash)
        uid = u.id if u else 0
    if not ok:
        c.hub.audit("AUTH", "로그인 실패", None, username=body.username)
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
    token, exp = issue_token(c.secret_key, uid, body.username, c.settings.session_ttl_hours)
    return {"token": token, "expires_at": exp, "username": body.username}


@router.get("/auth/me")
def auth_me(user: U) -> dict[str, Any]:
    return {"username": user["u"]}
