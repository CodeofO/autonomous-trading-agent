"""이벤트 허브: 감사 로그·채팅 메시지를 DB 에 남기고 WebSocket 구독자에게 실시간으로 뿌린다."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.core.clock import Clock
from app.db.models import AuditEvent, ChatMessage
from app.db.session import Database

log = logging.getLogger(__name__)

# 채팅 참여자 (UI 에서 아바타/색으로 구분)
ANALYST, RISK, BROKER, SYSTEM, USER = "analyst", "risk", "broker", "system", "user"


def _ser(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_ser(x) for x in v]
    return v


class EventHub:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()

    # ------------------------------------------------------------ 구독
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    def broadcast(self, msg: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # 느린 구독자는 오래된 메시지를 버린다
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    # ------------------------------------------------------------ 기록
    def audit(self, type_: str, message: str, symbol: str | None = None, **payload: Any) -> dict[str, Any]:
        ts = self.clock.now()
        with self.db.session() as s:
            ev = AuditEvent(ts=ts, type=type_, symbol=symbol, message=message, payload=_ser(payload))
            s.add(ev)
            s.flush()
            out = {
                "id": ev.id,
                "ts": ts.isoformat(),
                "type": type_,
                "symbol": symbol,
                "message": message,
                "payload": _ser(payload),
            }
        log.info("[%s] %s %s", type_, symbol or "", message)
        self.broadcast({"channel": "audit", "data": out})
        return out

    def chat(
        self, role: str, kind: str, content: str, run_id: int | None = None, **payload: Any
    ) -> dict[str, Any]:
        ts = self.clock.now()
        with self.db.session() as s:
            m = ChatMessage(
                ts=ts, role=role, kind=kind, content=content, run_id=run_id, payload=_ser(payload)
            )
            s.add(m)
            s.flush()
            out = {
                "id": m.id,
                "ts": ts.isoformat(),
                "role": role,
                "kind": kind,
                "content": content,
                "run_id": run_id,
                "payload": _ser(payload),
            }
        self.broadcast({"channel": "chat", "data": out})
        return out

    def typing(self, role: str, active: bool, label: str = "") -> None:
        """'입력 중…' 표시용 (DB 에 남기지 않음)."""
        self.broadcast({"channel": "typing", "data": {"role": role, "active": active, "label": label}})

    def state(self, data: dict[str, Any]) -> None:
        self.broadcast({"channel": "state", "data": _ser(data)})
