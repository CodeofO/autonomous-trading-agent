"""REST/WS API 통합 테스트."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.main import create_app
from tests.fake_toss import FakeTossClient

PW = "correct-horse-battery"


def _client(tmp_path: Path, start: datetime | None = None, **kw: Any) -> TestClient:
    s = Settings(
        database_url="sqlite://",
        data_dir=tmp_path,
        auth_enabled=kw.pop("auth_enabled", True),
        enable_scheduler=False,
        **kw,
    )
    # 장 시작 전(08:40) → 판단 주문이 09:00 체결 대기로 남아 취소·킬 스위치를 시험할 수 있다
    clock = SimulatedClock(start or datetime(2026, 11, 2, 8, 40, tzinfo=KST))
    return TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _client(tmp_path) as c:
        yield c


def _login(c: TestClient) -> dict[str, str]:
    r = c.post("/api/auth/setup", json={"username": "me", "password": PW})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait_run(c: TestClient, h: dict[str, str], n: int = 1, timeout: float = 20) -> list[dict[str, Any]]:
    end = time.time() + timeout
    while time.time() < end:
        runs = c.get("/api/agent/runs", headers=h).json()
        if len(runs) >= n and all(r["status"] != "RUNNING" for r in runs):
            return runs
        time.sleep(0.1)
    raise AssertionError("에이전트 세션이 끝나지 않음")


def test_auth_flow(client: TestClient) -> None:
    assert client.get("/api/auth/status").json() == {"auth_enabled": True, "setup_required": True}
    assert client.get("/api/portfolio").status_code == 401
    h = _login(client)
    assert client.get("/api/auth/status").json() == {"auth_enabled": True, "setup_required": False}
    assert client.post("/api/auth/setup", json={"username": "x", "password": PW}).status_code == 409
    assert (
        client.post("/api/auth/login", json={"username": "me", "password": "wrong-password"}).status_code
        == 401
    )
    r = client.post("/api/auth/login", json={"username": "me", "password": PW})
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=h).json() == {"username": "me"}
    bad = {"Authorization": h["Authorization"][:-3] + "abc"}
    assert client.get("/api/portfolio", headers=bad).status_code == 401
    assert client.post("/api/auth/setup", json={"username": "me", "password": "short"}).status_code == 422


def test_system_and_portfolio(client: TestClient) -> None:
    h = _login(client)
    s = client.get("/api/system", headers=h).json()
    assert s["trading_mode"] == "paper" and s["data_source"] == "toss" and s["agent_interval_minutes"] == 60
    assert s["llm"]["configured"] is False and "투자 권유" in s["disclaimer"]
    assert "09:00" in s["order_window"]
    pf = client.get("/api/portfolio", headers=h).json()
    assert pf["equity"] == 10_000_000 and pf["positions"] == []
    assert client.get("/api/openapi.json").status_code == 200


def test_agent_run_orders_cancel_kill(client: TestClient) -> None:
    h = _login(client)
    assert client.post("/api/agent/run", headers=h).status_code == 202
    runs = _wait_run(client, h)
    assert runs[0]["status"] == "DONE" and runs[0]["mode"] == "rule"
    detail = client.get(f"/api/agent/runs/{runs[0]['id']}", headers=h).json()
    assert detail["messages"] and detail["run"]["id"] == runs[0]["id"]
    open_orders = client.get("/api/orders?status=open", headers=h).json()
    assert open_orders, "규칙 에이전트가 주문을 내야 한다(시드 고정)"
    oid = open_orders[0]["id"]
    assert client.post(f"/api/orders/{oid}/cancel", headers=h).json()["status"] == "CANCELED"
    assert client.post(f"/api/orders/{oid}/cancel", headers=h).status_code == 409
    remaining = len(client.get("/api/orders?status=open", headers=h).json())
    r = client.post("/api/engine/kill", headers=h, json={"reason": "테스트"}).json()
    assert r == {"status": "KILLED", "canceled": remaining}
    assert client.get("/api/orders?status=open", headers=h).json() == []
    assert client.post("/api/agent/run", headers=h).status_code == 409
    assert client.post("/api/engine/resume", headers=h).json()["status"] == "RUNNING"
    events = client.get("/api/events?type=KILL_SWITCH", headers=h).json()
    assert len(events) == 1
    chat = client.get("/api/chat", headers=h).json()
    assert any("킬 스위치" in m["content"] for m in chat)
    assert {m["role"] for m in chat} >= {"analyst", "system", "broker"}


def test_risk_strategies_watchlist_settings(client: TestClient) -> None:
    h = _login(client)
    cfg = client.get("/api/risk", headers=h).json()["config"]
    assert cfg["max_position_weight"] == 0.2
    bad = {**cfg, "stop_loss": 0.05}
    assert client.put("/api/risk", headers=h, json=bad).status_code == 422
    ok = {**cfg, "max_order_amount": 2_000_000}
    assert client.put("/api/risk", headers=h, json=ok).json()["config"]["max_order_amount"] == 2_000_000
    strats = client.get("/api/strategies", headers=h).json()
    ma = next(s for s in strats if s["type"] == "ma_cross")
    assert "properties" in ma["schema"]
    assert (
        client.put(
            f"/api/strategies/{ma['id']}", headers=h, json={"params": {"short": 30, "long": 10}}
        ).status_code
        == 422
    )
    r = client.put(
        f"/api/strategies/{ma['id']}", headers=h, json={"params": {"short": 3, "long": 10}, "enabled": False}
    ).json()
    assert r["params"] == {"short": 3, "long": 10} and r["enabled"] is False
    assert client.post("/api/watchlist", headers=h, json={"symbol": "000660"}).status_code == 200
    assert client.post("/api/watchlist", headers=h, json={"symbol": "bad symbol!"}).status_code == 422
    wl = client.get("/api/watchlist", headers=h).json()
    assert wl[0]["symbol"] == "000660" and wl[0]["name"] == "SK하이닉스"
    client.delete("/api/watchlist/000660", headers=h)
    assert client.get("/api/watchlist", headers=h).json() == []
    st = client.put(
        "/api/agent/settings",
        headers=h,
        json={"name": "베타", "emoji": "🐢", "policy": "가치투자 위주로, 손실은 짧게 끊는다."},
    ).json()
    assert st["name"] == "베타"
    assert client.get("/api/system", headers=h).json()["persona"]["name"] == "베타"


def test_market_endpoints_and_backtest(client: TestClient) -> None:
    h = _login(client)
    uni = client.get("/api/market/universe", headers=h).json()
    assert any(u["symbol"] == "005930" for u in uni)
    cd = client.get("/api/market/005930/candles?days=60", headers=h).json()
    assert len(cd["bars"]) >= 40 and cd["name"] == "삼성전자"
    rs = client.get("/api/market/005930/research", headers=h).json()
    assert rs["financials"]["available"] is False and "DART" in rs["financials"]["reason"]  # 키 없음
    assert rs["supply"]["investor_net_buy_daily"] and rs["status"]["price_limits"]["upper"] > 0
    assert "items" in rs["news"]
    bt = client.post(
        "/api/backtest",
        headers=h,
        json={
            "strategy_type": "rsi",
            "params": {"period": 14, "lower": 35, "upper": 65},
            "symbols": ["005930", "005380", "247540"],
            "start": "2026-07-01",
            "end": "2026-10-30",
        },
    ).json()
    assert {"total_return_pct", "mdd_pct", "win_rate_pct", "trades", "equity_curve"} <= bt.keys()
    assert bt["trades"] > 0 and bt["equity_curve"][0]["equity"] == 10_000_000
    assert (
        client.post(
            "/api/backtest",
            headers=h,
            json={"strategy_type": "nope", "symbols": ["005930"], "start": "2026-01-01", "end": "2026-02-01"},
        ).status_code
        == 422
    )
    rep = client.post("/api/reports/now", headers=h).json()
    assert rep["equity"] == 10_000_000
    assert client.get("/api/reports", headers=h).json()[0]["day"] == "2026-11-02"


def test_chat_without_llm_and_websocket(client: TestClient) -> None:
    h = _login(client)
    token = h["Authorization"].split()[1]
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/events?token=bad") as ws:
        ws.receive_json()
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        assert ws.receive_json()["channel"] == "hello"
        assert client.post("/api/chat", headers=h, json={"message": "오늘 시장 어때?"}).status_code == 202
        seen = []
        for _ in range(5):
            msg = ws.receive_json()
            seen.append(msg)
            if msg["channel"] == "chat" and msg["data"]["role"] == "system":
                break
        assert any(m["channel"] == "chat" and m["data"]["role"] == "user" for m in seen)
        assert any(m["channel"] == "chat" and "LLM" in m["data"]["content"] for m in seen)
        ws.send_text("PING")
        assert ws.receive_json()["channel"] == "pong"


def test_secrets_never_exposed(tmp_path: Path) -> None:
    key = "sk-super-secret-value-9876"
    with _client(tmp_path, llm_base_url="https://llm.example.com/v1", llm_model="m", llm_api_key=key) as c:
        h = _login(c)
        body = c.get("/api/system", headers=h).text
        assert key not in body and "llm.example.com" in body
        assert key not in c.get("/api/openapi.json").text


def test_default_mode_needs_no_login(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False) as c:
        assert c.get("/api/auth/status").json() == {"auth_enabled": False, "setup_required": False}
        assert c.get("/api/portfolio").status_code == 200
        assert c.post("/api/engine/kill", json={"reason": "t"}).json()["status"] == "KILLED"
        with c.websocket_connect("/ws/events") as ws:
            assert ws.receive_json()["channel"] == "hello"
