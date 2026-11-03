"""휴대폰 알림(Web Push): 구독 관리, 감사 로그 → 알림 변환, 해지된 기기 정리, VAPID 서명 (실제 발송 없음)."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.engine import push as push_mod
from app.engine.push import BAD_KEY, NO_KEY, PushNotifier, allowed_endpoint, device_id
from app.engine.settings_store import SettingsStore
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

D = date(2026, 11, 2)
APPLE = "https://web.push.apple.com/QGtest-device-token"
GOOGLE = "https://fcm.googleapis.com/fcm/send/test-device-token"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _vapid_key() -> str:
    """./run.sh 와 같은 방식: P-256 위수보다 작은 32바이트 난수."""
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    while True:
        k = secrets.token_bytes(32)
        if 0 < int.from_bytes(k) < n:
            return _b64(k)


def _browser_keys() -> dict[str, str]:
    """브라우저가 구독할 때 주는 키 (p256dh 공개키 + auth 비밀값)."""
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    raw = pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": _b64(raw), "auth": _b64(secrets.token_bytes(16))}


class FakeSender:
    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.codes = codes or {}
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, info: dict[str, Any], body: str) -> int:
        self.sent.append((info["endpoint"], json.loads(body)))
        return self.codes.get(info["endpoint"], 201)


def _notifier(tmp_path: Path, **kw: Any) -> tuple[PushNotifier, FakeSender]:
    c = make_container(tmp_path, at(D, 10))
    sender = FakeSender(kw.pop("codes", None))
    n = PushNotifier(
        SettingsStore(c.db),
        _vapid_key(),
        "https://github.com",
        names=lambda s: "삼성전자",
        sender=sender,
        **kw,
    )
    return n, sender


def test_keys_and_endpoint_allowlist(tmp_path: Path) -> None:
    n, _ = _notifier(tmp_path)
    assert n.available and n.reason is None
    pk = base64.urlsafe_b64decode(n.public_key + "==")  # type: ignore[operator]
    assert len(pk) == 65 and pk[0] == 4  # 브라우저 applicationServerKey 형식

    store = n.kv
    assert not PushNotifier(store, None, "https://github.com").available
    assert PushNotifier(store, "", "https://github.com").reason == NO_KEY
    assert PushNotifier(store, "not-a-key", "https://github.com").reason == BAD_KEY

    assert allowed_endpoint(APPLE) and allowed_endpoint(GOOGLE)
    for bad in ("http://web.push.apple.com/x", "https://127.0.0.1/x", "https://push.apple.com.evil.test/x"):
        assert not allowed_endpoint(bad)
    with pytest.raises(ValueError):
        n.subscribe("https://intranet.test/hook", "p", "a", "x")


async def test_audit_events_become_notifications(tmp_path: Path) -> None:
    n, sender = _notifier(tmp_path)
    n.subscribe(APPLE, "p", "a", "iPhone · Safari")
    n.subscribe(APPLE, "p", "a", "iPhone · Safari")  # 같은 기기를 다시 구독해도 하나
    assert [d["label"] for d in n.state()["devices"]] == ["iPhone · Safari"]

    fill = {
        "type": "FILL",
        "message": "BUY 3주 @ 70,100.00",
        "symbol": "005930",
        "payload": {"price": 70100.0, "quantity": 3},
    }
    assert n.message(fill) == (
        "fills",
        {"title": "매수 체결", "body": "삼성전자 3주 @ 70,100원", "url": "/portfolio"},
    )
    block = {
        "type": "RISK_BLOCK",
        "message": "1회 주문 한도 초과",
        "symbol": "005930",
        "payload": {"side": "BUY", "quantity": 10},
    }
    assert n.message(block) == (
        "risk",
        {"title": "🛑 리스크 차단", "body": "삼성전자 매수 10주 — 1회 주문 한도 초과", "url": "/room"},
    )
    assert n.message({"type": "AGENT_RUN", "message": "세션 #4 DONE", "payload": {"run_id": 4}}) == (
        "agent",
        {"title": "🤖 판단 세션 끝", "body": "세션 #4 완료", "url": "/room"},
    )
    assert n.message({"type": "CONFIG", "message": "리스크 설정 변경"}) is None

    await n.broadcast(n.message(fill)[1])  # type: ignore[index]
    assert sender.sent == [
        (APPLE, {"title": "매수 체결", "body": "삼성전자 3주 @ 70,100원", "url": "/portfolio"})
    ]
    assert n.state()["devices"][0]["last_ok"] is not None

    live, _ = _notifier(tmp_path / "live", live=True)
    assert live.message({"type": "KILL_SWITCH", "message": "킬 스위치 작동: 수동"})[1]["title"] == (  # type: ignore[index]
        "[실거래] 🛑 킬 스위치 작동"
    )


async def test_hub_sends_only_enabled_categories(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    sender = FakeSender()
    c.push = PushNotifier(c.kv, _vapid_key(), "https://github.com", names=c.store.name_of, sender=sender)
    c.hub.listeners = [c.push.on_audit]
    c.hub.audit(
        "FILL", "SELL 2주 @ 70,000.00", "005930", price=70000.0, quantity=2
    )  # 구독 기기 없음 → 안 보냄
    c.push.subscribe(GOOGLE, "p", "a", "Android")
    c.hub.audit("AGENT_RUN", "세션 #1 DONE", None, run_id=1)  # 기본값 꺼짐
    c.hub.audit("KILL_SWITCH", "킬 스위치 작동: 수동", None)
    c.push.set_prefs({"alerts": False})
    c.hub.audit("ERROR", "스케줄 작업 실패: sync", None)
    for t in list(c.push._tasks):
        await t
    assert [p["title"] for _, p in sender.sent] == ["🛑 킬 스위치 작동"]
    assert c.push.state()["prefs"] == {"fills": True, "risk": True, "alerts": False, "agent": False}


async def test_unsubscribed_devices_are_removed(tmp_path: Path) -> None:
    n, _ = _notifier(tmp_path, codes={APPLE: 410, GOOGLE: 500})
    n.subscribe(APPLE, "p", "a", "iPhone")
    n.subscribe(GOOGLE, "p", "a", "Android")
    assert await n.broadcast({"title": "t", "body": "b", "url": "/"}) == {
        "sent": 0,
        "failed": 2,
        "removed": 1,
    }
    assert [d["label"] for d in n.state()["devices"]] == ["Android"]  # 일시 오류(500)는 남긴다
    assert n.remove_device(device_id(GOOGLE)) == 1 and n.state()["devices"] == []


async def test_webpush_signs_with_vapid_and_encrypts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 라이브러리 경로: 요청은 가로채고(외부 호출 없음) 서명·암호화 헤더만 확인한다."""
    c = make_container(tmp_path, at(D, 10))
    n = PushNotifier(c.kv, _vapid_key(), "https://github.com")
    seen: dict[str, Any] = {}

    class Resp:
        status_code = 201
        text = ""
        headers: dict[str, str] = {}

    def fake_post(url: str, **kw: Any) -> Resp:
        seen.update(url=url, **kw)
        return Resp()

    monkeypatch.setattr(push_mod.webpush.__globals__["requests"], "post", fake_post)
    keys = _browser_keys()
    n.subscribe(APPLE, keys["p256dh"], keys["auth"], "iPhone")
    assert await n.broadcast({"title": "🔔", "body": "테스트", "url": "/"}) == {
        "sent": 1,
        "failed": 0,
        "removed": 0,
    }
    h = {k.lower(): v for k, v in seen["headers"].items()}
    assert seen["url"] == APPLE and h["authorization"].startswith("vapid t=")
    assert f"k={n.public_key}" in h["authorization"]
    assert (
        h["content-encoding"] == "aes128gcm" and h["urgency"] == "high" and h["ttl"] == str(push_mod.TTL_SEC)
    )
    assert "테스트".encode() not in seen["data"]  # 본문은 암호화돼서 나간다


def test_push_api(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, vapid_private_key="") as client:
        r = client.get("/api/push")
        assert r.status_code == 200 and r.json()["available"] is False and r.json()["reason"] == NO_KEY
        assert client.post("/api/push/test", json={}).status_code == 409

    with _client(tmp_path / "on", auth_enabled=False, vapid_private_key=_vapid_key()) as client:
        sender = FakeSender()
        client.app.state.container.push._send = sender  # type: ignore[attr-defined]
        st = client.get("/api/push").json()
        assert st["available"] and st["public_key"] and st["devices"] == []
        sub = {"endpoint": APPLE, "keys": _browser_keys(), "label": "iPhone · Safari"}
        assert (
            client.post(
                "/api/push/subscribe", json={**sub, "endpoint": "https://intranet.test/x"}
            ).status_code
            == 400
        )
        r = client.post("/api/push/subscribe", json=sub)
        assert r.status_code == 200 and r.json() == {"id": device_id(APPLE), "devices": 1}
        assert APPLE not in client.get("/api/push").text  # 기기별 비밀 주소는 응답에 안 나간다

        assert client.post("/api/push/test", json={"endpoint": APPLE}).json() == {
            "sent": 1,
            "failed": 0,
            "removed": 0,
        }
        assert sender.sent[0][1]["title"] == "🔔 테스트 알림"
        r = client.put("/api/push/prefs", json={"fills": False, "risk": True, "alerts": True, "agent": True})
        assert r.json() == {"fills": False, "risk": True, "alerts": True, "agent": True}
        assert client.post("/api/push/unsubscribe", json={"endpoint": APPLE}).json() == {"removed": 1}
        assert client.delete(f"/api/push/devices/{device_id(APPLE)}").json() == {"removed": 0}
