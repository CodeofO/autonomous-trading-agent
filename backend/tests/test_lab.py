"""전략 실험실 (D-62): 전략 카탈로그, 주 계좌 전략 고르기, 시나리오마다 따로인 모의계좌(실거래 차단),
주 계좌 판단 뒤 차례로 판단, 비교 화면, 다시 시작, 파일 DB 마이그레이션. 네트워크·LLM 호출 없음 (규칙 기반)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.brokers.base import OrderRequest, OrderSide
from app.brokers.paper import PaperBroker
from app.container import scenario_db_url
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.core.http import LiveOrderBlockedError
from app.engine.scenarios import LabError
from app.strategies import styles
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

D = date(2026, 11, 2)


def _last_chat(c: Any) -> str:
    from sqlalchemy import select

    from app.db.models import ChatMessage

    with c.db.session() as s:
        return str(s.scalars(select(ChatMessage.content).order_by(ChatMessage.id.desc()).limit(1)).first())


def _stub(calls: list[str], name: str) -> Any:
    async def run() -> None:
        calls.append(name)

    return run


def test_catalog_has_named_strategies_with_market_specific_data_notes() -> None:
    ids = list(styles.STYLES)
    assert ids[0] == styles.DEFAULT_ID and {"lynch", "buffett", "graham", "momentum"} <= set(ids)
    lynch = styles.STYLES["lynch"]
    assert "PEG" in lynch.policy(us=False) and "get_financials" in lynch.policy(us=False)
    assert "재무제표 도구가 없다" in lynch.policy(us=True)  # 미장에는 DART 가 없다
    by = {x["id"]: x for x in styles.catalog()}
    assert by[styles.DEFAULT_ID]["policy"] is None and "해자" in by["buffett"]["policy"]


def test_main_strategy_drives_the_manager_policy(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    default_policy = c.runner.policy()
    assert c.lab.main_id() == styles.DEFAULT_ID
    c.lab.set_main("buffett")
    assert "워런 버핏" in c.runner.policy() and c.runner.policy() != default_policy
    assert "'워런 버핏' 전략으로 운용" in _last_chat(c)
    with pytest.raises(LabError):
        c.lab.set_main("soros")
    c.lab.set_main(styles.DEFAULT_ID)
    assert c.runner.policy() == default_policy


async def test_scenarios_get_their_own_paper_accounts(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    assert c.lab.set_enabled(["buffett", "lynch"]) == ["lynch", "buffett"]  # 카탈로그 순서
    lynch, buffett = c.lab.stacks["lynch"], c.lab.stacks["buffett"]
    assert lynch.db is not c.db and lynch.db is not buffett.db  # 기록 DB 가 따로
    assert lynch.store is c.store and lynch.cal is c.cal  # 시세·캘린더는 같이
    assert isinstance(lynch.broker, PaperBroker) and lynch.broker.trading_mode == "paper"
    assert lynch.runner.persona()["name"] == "린치" and "피터 린치" in lynch.runner.policy()
    assert lynch.research.google is None  # 유료 검색은 시나리오에 붙이지 않는다
    assert not lynch.push.available
    # 시나리오 계좌는 실거래로 바꿀 수 없다 — 모의가 아니면 주문 자체를 거부
    lynch.broker.trading_mode = "live"
    with pytest.raises(LiveOrderBlockedError):
        await lynch.broker.place_order(
            OrderRequest(client_order_id="x", symbol="005930", side=OrderSide.BUY, quantity=1)
        )
    lynch.broker.trading_mode = "paper"
    # 한 시나리오 계좌의 현금이 바뀌어도 다른 계좌·주 계좌는 그대로
    lynch.broker.state["cash"] -= 1_000_000
    view = await c.lab.view()
    by = {r["id"]: r for r in view["rows"] if r["kind"] == "scenario"}
    assert by["lynch"]["equity"] == c.settings.paper_initial_cash - 1_000_000
    assert by["buffett"]["equity"] == c.settings.paper_initial_cash
    assert by["lynch"]["return_pct"] == -10.0 and by["lynch"]["live"] is False
    assert view["rows"][0]["kind"] == "main" and view["rows"][0]["id"] == styles.DEFAULT_ID
    # 끄면 스택은 내려가고, 다시 켜면 같은 기록으로 이어서
    c.lab.set_enabled(["buffett"])
    assert set(c.lab.stacks) == {"buffett"}
    with pytest.raises(LabError):
        c.lab.set_enabled(["lynch", "nope"])


async def test_scenarios_decide_after_the_main_decision_in_turn(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.lab.set_enabled(["lynch", "graham"])
    calls: list[str] = []
    c.engine.hourly = _stub(calls, "main")
    for sid, st in c.lab.stacks.items():
        st.engine.hourly = _stub(calls, sid)
        st.engine.morning = _stub(calls, f"{sid}:morning")
    await c.scheduler._job("hourly")()
    for t in list(c.scheduler._tasks):
        await t
    assert calls == ["main", "lynch", "graham"]
    calls.clear()
    assert await c.lab.run_round("morning") == ["lynch", "graham"]
    assert calls == ["lynch:morning", "graham:morning"]
    # 지난 차례가 아직 돌고 있으면 이번 차례는 건너뛴다
    async with c.lab._round:
        assert await c.lab.run_round("hourly") == []


async def test_scenario_runs_rule_based_without_llm_and_keeps_records_apart(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.lab.set_enabled(["momentum"])
    st = c.lab.stacks["momentum"]
    assert await c.lab.run_round("hourly") == ["momentum"]
    detail = await c.lab.detail("momentum")
    assert len(detail["runs"]) == 1 and detail["runs"][0]["status"] == "DONE"
    with c.db.session() as s:  # 주 계좌 기록에는 남지 않는다
        from sqlalchemy import func, select

        from app.db.models import AgentRun

        assert (s.scalar(select(func.count(AgentRun.id))) or 0) == 0
    row = next(r for r in (await c.lab.view())["rows"] if r["id"] == "momentum")
    assert row["runs"] == 1 and row["last_run"]["status"] == "DONE"
    assert st.runner.llm is c.runner.llm


async def test_reset_starts_the_scenario_account_over(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.lab.set_enabled(["graham"])
    st = c.lab.stacks["graham"]
    st.broker.state["cash"] = 1.0
    c.lab.reset("graham")
    assert (await st.broker.get_balance()).equity == c.settings.paper_initial_cash
    with pytest.raises(LabError):
        c.lab.reset("buffett")  # 켜지 않은 시나리오


def test_lab_api_per_market(tmp_path: Path) -> None:
    with _client(tmp_path, datetime(2026, 11, 4, 12, 0, tzinfo=KST), auth_enabled=False) as c:
        lab = c.get("/api/lab").json()
        assert lab["market"] == "KR" and lab["main"] == "default" and lab["enabled"] == []
        assert [r["kind"] for r in lab["rows"]] == ["main"]
        assert c.put("/api/lab/scenarios", json={"enabled": ["lynch", "zzz"]}).status_code == 400
        on = c.put("/api/lab/scenarios", json={"enabled": ["lynch", "buffett"]}).json()
        assert on["enabled"] == ["lynch", "buffett"] and len(on["rows"]) == 3
        assert all(r["live"] is False for r in on["rows"])
        assert c.put("/api/lab/main", json={"id": "graham"}).json()["main"] == "graham"
        assert "전략으로 운용" in c.get("/api/chat").json()[-1]["content"]
        detail = c.get("/api/lab/scenarios/lynch").json()
        assert detail["positions"] == [] and detail["runs"] == []
        assert c.get("/api/lab/scenarios/graham").status_code == 404  # 켜지 않은 시나리오
        assert c.post("/api/lab/scenarios/lynch/run").status_code == 202
        assert c.post("/api/lab/scenarios/lynch/reset").status_code in (200, 409)
        # 미장은 따로 (국장 설정과 섞이지 않는다)
        us = c.get("/api/us/lab").json()
        assert us["market"] == "US" and us["currency"] == "USD" and us["enabled"] == []
        us_on = c.put("/api/us/lab/scenarios", json={"enabled": ["buffett"]}).json()
        assert us_on["rows"][1]["initial_cash"] == us_on["rows"][0]["initial_cash"]
        assert c.get("/api/lab").json()["enabled"] == ["lynch", "buffett"]


def test_scenario_db_path_and_file_migration(tmp_path: Path) -> None:
    pg = Settings(_env_file=None, database_url="postgresql+psycopg://x@db/x", data_dir=tmp_path)  # type: ignore[call-arg]
    assert scenario_db_url(pg, "US", "lynch") == f"sqlite:///{tmp_path / 'scenarios' / 'us_lynch.db'}"
    mem = Settings(_env_file=None, database_url="sqlite://", data_dir=tmp_path)  # type: ignore[call-arg]
    assert scenario_db_url(mem, "KR", "lynch") == "sqlite://"

    from app.main import create_app
    from tests.fake_toss import FakeTossClient

    clock = SimulatedClock(datetime(2026, 11, 4, 12, 0, tzinfo=KST))
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'kr.db'}",
        data_dir=tmp_path,
        enable_scheduler=False,
        auth_enabled=False,
    )
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        assert c.put("/api/lab/scenarios", json={"enabled": ["buffett"]}).status_code == 200
        assert c.get("/api/lab/scenarios/buffett").status_code == 200
    assert (tmp_path / "scenarios" / "kr_buffett.db").exists()
    # 다시 켜도(재시작) 켠 시나리오는 그대로 준비된다
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        assert set(c.app.state.container.lab.stacks) == {"buffett"}  # type: ignore[attr-defined]


def test_strategy_has_its_own_risk_limits(tmp_path: Path) -> None:
    """리스크 한도도 전략마다 (D-64): 바꾸면 그 전략 한도로, 고쳐 둔 한도는 전략별로 기억, 금액 한도는 그대로."""
    c = make_container(tmp_path, at(D, 10))
    default = c.engine.risk.config
    c.lab.set_main("buffett")
    r = c.engine.risk.config
    assert (r.stop_loss, r.take_profit, r.min_hold_days, r.max_positions) == (-0.30, 3.0, 90, 8)
    assert r.max_order_amount == default.max_order_amount
    assert "최소 보유 90일" in _last_chat(c)
    c.engine.set_risk_config(r.model_copy(update={"stop_loss": -0.25}))  # 버핏으로 운용하며 고친 값
    c.lab.set_main("default")
    assert c.engine.risk.config == default  # 기본 매니저로 돌아오면 원래 한도
    c.lab.set_main("buffett")
    assert c.engine.risk.config.stop_loss == -0.25  # 고쳐 둔 버핏 한도를 기억
    assert c.lab.apply_preset().stop_loss == -0.30  # 전략 기본값으로 되돌리기


async def test_scenarios_use_their_strategy_risk_preset(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    c.lab.set_enabled(["lynch", "momentum"])
    lynch = c.lab.stacks["lynch"].engine.risk.config
    momentum = c.lab.stacks["momentum"].engine.risk.config
    assert (lynch.stop_loss, lynch.min_hold_days) == (-0.20, 30)
    assert (momentum.stop_loss, momentum.min_hold_days) == (-0.08, 0)
    assert lynch.max_order_amount == c.engine.risk.config.max_order_amount
    rows = {r["id"]: r for r in (await c.lab.view())["rows"]}
    assert "최소 보유 30일" in rows["lynch"]["risk"]["text"]
    assert next(s for s in styles.catalog() if s["id"] == "buffett")["risk"]["min_hold_days"] == 90


def test_risk_api_shows_strategy_preset(tmp_path: Path) -> None:
    with _client(tmp_path, datetime(2026, 11, 4, 12, 0, tzinfo=KST), auth_enabled=False) as c:
        r = c.get("/api/risk").json()
        assert r["strategy"]["id"] == "default" and r["custom"] is False
        c.put("/api/lab/main", json={"id": "lynch"})
        r = c.get("/api/risk").json()
        assert (
            r["strategy"]["name"] == "피터 린치"
            and r["config"]["min_hold_days"] == 30
            and r["custom"] is False
        )
        changed = {**r["config"], "stop_loss": -0.1}
        assert c.put("/api/risk", json=changed).json()["custom"] is True
        back = c.post("/api/risk/preset").json()
        assert back["config"]["stop_loss"] == -0.2 and back["custom"] is False
        assert c.get("/api/us/risk").json()["config"]["max_order_amount"] == 1500  # 미장은 따로
