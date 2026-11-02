"""실시간 이벤트 WebSocket — 채팅·감사로그·상태·입력중 표시를 브라우저로 전달."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.auth import verify_token
from app.container import Container

router = APIRouter()


@router.websocket("/ws/events")
async def events_ws(ws: WebSocket) -> None:
    c: Container = ws.app.state.container
    # 브라우저 WebSocket 은 헤더를 못 붙이므로 쿼리 토큰 사용 (로컬 전용, 로그에 남기지 않음)
    token = ws.query_params.get("token", "")
    if verify_token(c.secret_key, token) is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    q = c.hub.subscribe()
    await ws.send_json(
        {"channel": "hello", "data": {"engine_status": c.engine.status, "agent_running": c.runner.running}}
    )

    async def pump() -> None:
        while True:
            msg = await q.get()
            await ws.send_json(msg)

    async def recv() -> None:
        while True:
            text = await ws.receive_text()
            if text.strip().upper() == "PING":
                await ws.send_json({"channel": "pong", "data": {}})

    tasks = [asyncio.create_task(pump()), asyncio.create_task(recv())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await t
        c.hub.unsubscribe(q)
