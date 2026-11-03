"""휴대폰 알림 (Web Push, D-39).

홈 화면에 추가한 웹앱(iOS 16.4+)·안드로이드·데스크톱 브라우저가 구독하면, 감사 로그 중 체결·리스크 차단·
킬 스위치·오류(선택: 에이전트 판단 완료·일일 리포트)를 알림으로 보낸다. 구독 목록과 알림 종류는
app_settings 'push' 에 두고, VAPID 개인키는 .env 로만 받는다. 구독 주소(endpoint)는 기기별 비밀 주소라
API 응답·로그에 내보내지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from app.engine.settings_store import SettingsStore

log = logging.getLogger(__name__)

KEY = "push"
DEFAULT_PREFS: dict[str, bool] = {"fills": True, "risk": True, "alerts": True, "agent": False}
# 서버가 임의 주소로 요청을 보내지 않도록, 구독 주소는 브라우저 푸시 서비스로만 받는다
PUSH_HOSTS = (
    "push.apple.com",
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)
GONE = (404, 410)  # 구독이 해지된 기기 → 목록에서 지운다
MAX_DEVICES = 20
TTL_SEC = 6 * 3600  # 휴대폰이 꺼져 있어도 6시간 안에 켜지면 받는다

Sender = Callable[[dict[str, Any], str], Awaitable[int]]  # (구독 정보, 본문) → HTTP 상태 코드
NO_KEY = ".env 에 VAPID_PRIVATE_KEY 가 없어요. ./run.sh 로 다시 실행하면 자동으로 만들어 넣어요."
BAD_KEY = ".env 의 VAPID_PRIVATE_KEY 형식이 올바르지 않아요. 줄을 지우고 ./run.sh 로 다시 실행하세요."


def allowed_endpoint(url: str) -> bool:
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def device_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:12]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _side_ko(side: str) -> str:
    return "매수" if side.upper() == "BUY" else "매도" if side.upper() == "SELL" else side


class PushNotifier:
    def __init__(
        self,
        kv: SettingsStore,
        private_key: str | None,
        subject: str,
        *,
        names: Callable[[str], str] | None = None,
        now: Callable[[], datetime] | None = None,
        live: bool = False,
        sender: Sender | None = None,
    ) -> None:
        self.kv = kv
        self.subject = subject
        self.names = names or (lambda s: s)
        self.now = now or (lambda: datetime.now().astimezone())
        self.live = live
        self.reason: str | None = None
        self._vapid: Any = None
        if private_key and private_key.strip():
            try:
                self._vapid = Vapid.from_string(private_key=private_key.strip())
            except Exception:
                self.reason = BAD_KEY
                log.warning("휴대폰 알림 꺼짐: VAPID_PRIVATE_KEY 형식 오류")
        else:
            self.reason = NO_KEY
        self._send: Sender = sender or self._webpush
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ 상태
    @property
    def available(self) -> bool:
        return self._vapid is not None

    @property
    def public_key(self) -> str | None:
        if self._vapid is None:
            return None
        raw = self._vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return _b64(raw)

    def _data(self) -> dict[str, Any]:
        d = self.kv.get(KEY)
        return {"subs": list(d.get("subs", [])), "prefs": {**DEFAULT_PREFS, **d.get("prefs", {})}}

    def state(self) -> dict[str, Any]:
        d = self._data()
        return {
            "available": self.available,
            "reason": self.reason,
            "public_key": self.public_key,
            "devices": [
                {
                    "id": device_id(s["endpoint"]),
                    "label": s.get("label", ""),
                    "created_at": s.get("created_at"),
                    "last_ok": s.get("last_ok"),
                }
                for s in d["subs"]
            ],
            "prefs": d["prefs"],
        }

    # ------------------------------------------------------------ 구독 관리
    def subscribe(self, endpoint: str, p256dh: str, auth: str, label: str) -> str:
        if not allowed_endpoint(endpoint):
            raise ValueError("브라우저 푸시 서비스 주소가 아니에요")
        d = self._data()
        subs = [s for s in d["subs"] if s["endpoint"] != endpoint]
        subs.append(
            {
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
                "label": label[:60],
                "created_at": self.now().isoformat(),
                "last_ok": None,
            }
        )
        self.kv.set(KEY, {**d, "subs": subs[-MAX_DEVICES:]})
        return device_id(endpoint)

    def _remove(self, keep: Callable[[dict[str, Any]], bool]) -> int:
        d = self._data()
        subs = [s for s in d["subs"] if keep(s)]
        self.kv.set(KEY, {**d, "subs": subs})
        return len(d["subs"]) - len(subs)

    def unsubscribe(self, endpoint: str) -> int:
        return self._remove(lambda s: s["endpoint"] != endpoint)

    def remove_device(self, did: str) -> int:
        return self._remove(lambda s: device_id(s["endpoint"]) != did)

    def set_prefs(self, prefs: dict[str, bool]) -> dict[str, bool]:
        d = self._data()
        merged = {k: bool(prefs.get(k, v)) for k, v in d["prefs"].items() if k in DEFAULT_PREFS}
        self.kv.set(KEY, {**d, "prefs": merged})
        return merged

    # ------------------------------------------------------------ 감사 로그 → 알림
    def message(self, ev: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
        """(알림 종류, 알림 내용). 알릴 이벤트가 아니면 None."""
        t, msg, sym = ev.get("type"), str(ev.get("message", "")), ev.get("symbol")
        p = ev.get("payload") or {}
        name = self.names(sym) if sym else ""
        out: tuple[str, dict[str, str]] | None = None
        if t == "FILL":
            side = _side_ko(msg.split(" ", 1)[0])
            qty, price = p.get("quantity", 0), float(p.get("price") or 0)
            out = (
                "fills",
                {"title": f"{side} 체결", "body": f"{name} {qty:,}주 @ {price:,.0f}원", "url": "/portfolio"},
            )
        elif t == "RISK_BLOCK":
            what = f"{name} {_side_ko(str(p.get('side', '')))} {p.get('quantity', '')}주".strip()
            out = ("risk", {"title": "🛑 리스크 차단", "body": f"{what} — {msg}", "url": "/room"})
        elif t == "KILL_SWITCH":
            out = ("alerts", {"title": "🛑 킬 스위치 작동", "body": msg, "url": "/", "tag": "kill"})
        elif t == "ERROR":
            out = ("alerts", {"title": "⚠️ 오류", "body": msg, "url": "/logs"})
        elif t == "AGENT_RUN":
            status = {"DONE": "완료", "FAILED": "실패"}.get(
                str(msg.rsplit(" ", 1)[-1]), msg.rsplit(" ", 1)[-1]
            )
            out = (
                "agent",
                {"title": "🤖 판단 세션 끝", "body": f"세션 #{p.get('run_id', '')} {status}", "url": "/room"},
            )
        elif t == "REPORT":
            out = ("agent", {"title": "📊 일일 리포트", "body": msg, "url": "/"})
        if out and self.live:
            out[1]["title"] = "[실거래] " + out[1]["title"]
        return out

    def on_audit(self, ev: dict[str, Any]) -> None:
        if not self.available:
            return
        m = self.message(ev)
        if m is None:
            return
        category, payload = m
        d = self._data()
        if not d["subs"] or not d["prefs"].get(category):
            return
        self._schedule(self.broadcast(payload))

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """동기 API(스레드풀)에서 남긴 감사 로그도 알림을 보낼 수 있도록 서버 이벤트 루프를 기억한다."""
        self._loop = loop

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            t = asyncio.get_running_loop().create_task(coro)
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        except RuntimeError:
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self._loop)
            else:
                coro.close()

    # ------------------------------------------------------------ 발송
    async def broadcast(self, payload: dict[str, str], endpoint: str | None = None) -> dict[str, int]:
        subs = [s for s in self._data()["subs"] if endpoint is None or s["endpoint"] == endpoint]
        if not self.available or not subs:
            return {"sent": 0, "failed": 0, "removed": 0}
        body = json.dumps(payload, ensure_ascii=False)
        codes = await asyncio.gather(*(self._one(s, body) for s in subs))
        ok = {s["endpoint"] for s, c in zip(subs, codes, strict=True) if 200 <= c < 300}
        gone = {s["endpoint"] for s, c in zip(subs, codes, strict=True) if c in GONE}
        if ok or gone:
            now = self.now().isoformat()
            d = self._data()
            subs_now = [
                {**s, "last_ok": now} if s["endpoint"] in ok else s
                for s in d["subs"]
                if s["endpoint"] not in gone
            ]
            self.kv.set(KEY, {**d, "subs": subs_now})
        return {"sent": len(ok), "failed": len(subs) - len(ok), "removed": len(gone)}

    async def _one(self, sub: dict[str, Any], body: str) -> int:
        info = {"endpoint": sub["endpoint"], "keys": sub["keys"]}
        try:
            return await self._send(info, body)
        except Exception as e:  # 네트워크 오류 등 — 다른 기기 발송은 계속
            log.warning(
                "휴대폰 알림 발송 실패 (%s): %s", urlsplit(sub["endpoint"]).hostname, type(e).__name__
            )
            return 0

    async def _webpush(self, info: dict[str, Any], body: str) -> int:
        def call() -> int:
            try:
                r = webpush(
                    info,
                    body,
                    vapid_private_key=self._vapid,
                    vapid_claims={"sub": self.subject},  # aud 는 기기별로 채워지므로 매번 새 dict
                    ttl=TTL_SEC,
                    timeout=10,
                    headers={"Urgency": "high"},
                )
                return int(r.status_code)
            except WebPushException as e:
                code = e.status_code or 0
                log.warning("휴대폰 알림 거절 (%s): HTTP %s", urlsplit(info["endpoint"]).hostname, code)
                return int(code)

        return await asyncio.to_thread(call)
