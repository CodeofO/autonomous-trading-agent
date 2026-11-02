"""엔진·에이전트 통합 테스트 (MockWorld + 규칙 에이전트 / respx 로 가짜 LLM)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.brokers.base import OrderStatus
from app.container import Container, build_container
from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.db.models import AgentDecision, AgentRun, ChatMessage, DailyReport, Fill, Order
from app.engine.state_machine import TRANSITIONS

LLM_URL = "https://llm.test/v1/chat/completions"


def make(tmp_path: Path, **kw: Any) -> Container:
    s = Settings(
        database_url="sqlite://",
        data_dir=tmp_path,
        sim_start="2026-11-02T08:00:00",
        enable_scheduler=False,
        **kw,
    )
    return build_container(s, create_schema=True)


def _chat(c: Container) -> list[ChatMessage]:
    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage).order_by(ChatMessage.id)))


def _orders(c: Container) -> list[Order]:
    with c.db.session() as s:
        return list(s.scalars(select(Order).order_by(Order.id)))


async def _simulate(c: Container, until: datetime, step_min: int = 10) -> None:
    c.scheduler.sim_speed = step_min * 60
    clock = c.clock
    assert isinstance(clock, SimulatedClock)
    while clock.now() < until:
        await c.scheduler.sim_tick()


# ------------------------------------------------------------------ 하루치 시뮬레이션 E2E
async def test_two_day_simulation_with_rule_agent(tmp_path: Path) -> None:
    c = make(tmp_path)
    assert c.data_source == "mock" and c.clock_mode == "simulated"
    await _simulate(c, datetime(2026, 11, 4, 16, 0, tzinfo=KST))

    with c.db.session() as s:
        runs = list(s.scalars(select(AgentRun)))
        decisions = list(s.scalars(select(AgentDecision)))
        reports = list(s.scalars(select(DailyReport)))
        fills = list(s.scalars(select(Fill)))
    assert [r.trigger for r in runs] == ["morning", "afternoon"] * 3
    assert all(r.status == "DONE" and r.mode == "rule" for r in runs)
    assert decisions, "결정이 기록돼야 한다"
    assert len(reports) == 3
    orders = _orders(c)
    assert orders, "규칙 에이전트가 적어도 한 번은 주문해야 한다(시드 고정)"
    for o in orders:
        assert o.status in {s.value for s in TRANSITIONS}
        if o.status != "REJECTED":
            # 접수 시각은 반드시 주문 가능 창 안
            assert (
                c.cal.fill_basis(
                    o.created_at.replace(tzinfo=KST) if o.created_at.tzinfo is None else o.created_at
                )
                is not None
            )
    # 11/2·11/3 주문은 11/4 13:20 동기화까지 체결 확정돼야 한다
    early = [o for o in orders if o.trade_day and o.trade_day.isoformat() < "2026-11-04"]
    assert early and all(o.status in ("FILLED", "CANCELED", "REJECTED") for o in early)
    assert fills
    roles = {m.role for m in _chat(c)}
    assert {"analyst", "broker", "system"} <= roles
    kinds = {m.kind for m in _chat(c)}
    assert {"tool", "decision", "summary", "fill"} <= kinds
    pf = await c.engine.portfolio()
    assert pf["equity"] > 0 and pf["latest_price_day"] == "2026-11-03"


async def test_kill_switch_cancels_and_blocks(tmp_path: Path) -> None:
    c = make(tmp_path)
    await _simulate(c, datetime(2026, 11, 2, 8, 50, tzinfo=KST))
    open_before = c.engine.open_order_count()
    n = await c.engine.kill("테스트")
    assert n == open_before and c.engine.open_order_count() == 0
    assert c.engine.status == "KILLED"
    assert await c.engine.run_agent("manual") is None  # 정지 상태에선 세션 실행 안 함
    from app.risk.rules import OrderIntent

    order, decision = await c.engine.orders.submit(OrderIntent("MOCK01", "BUY", 1, 70_000))
    assert order is None and decision.rule == "kill_switch"
    c.engine.resume()
    assert c.engine.status == "RUNNING"
    texts = " ".join(m.content for m in _chat(c))
    assert "킬 스위치" in texts


# ------------------------------------------------------------------ 가짜 LLM (tool calling)
def _tc(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _resp(content: str | None, calls: list[dict[str, Any]] | None = None) -> httpx.Response:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return httpx.Response(
        200,
        json={
            "choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


@respx.mock
async def test_llm_tool_loop_places_risk_checked_orders(tmp_path: Path) -> None:
    c = make(
        tmp_path, llm_base_url="https://llm.test/v1", llm_model="test-model", llm_api_key="sk-test-123456"
    )
    await _simulate(c, datetime(2026, 11, 2, 8, 30, tzinfo=KST))  # 장전 주문 창
    c.engine.start_of_day_equity((await c.broker.get_balance()).equity)
    await c.engine.sync_data()
    route = respx.post(LLM_URL).mock(
        side_effect=[
            _resp("포트폴리오부터 볼게요 💼", [_tc("get_portfolio", {}, 1)]),
            _resp(
                "가온전자 재무와 뉴스를 같이 볼게요",
                [
                    _tc("get_financials", {"symbol": "MOCK01"}, 2),
                    _tc("search_news", {"query": "가온전자"}, 3),
                    _tc("get_price_history", {"symbol": "MOCK01", "days": 60}, 4),
                ],
            ),
            _resp(
                None,
                [
                    _tc(
                        "submit_decisions",
                        {
                            "market_view": "중립",
                            "summary": "가온전자 담고 한빛반도체는 크게 사볼게요!",
                            "decisions": [
                                {
                                    "symbol": "MOCK01",
                                    "action": "BUY",
                                    "target_weight": 0.08,
                                    "confidence": 0.7,
                                    "rationale": "실적 안정",
                                    "evidence": ["PER 확인"],
                                },
                                {
                                    "symbol": "MOCK02",
                                    "action": "BUY",
                                    "quantity": 100000,
                                    "confidence": 0.9,
                                    "rationale": "과감히",
                                },
                                {
                                    "symbol": "MOCK03",
                                    "action": "HOLD",
                                    "confidence": 0.3,
                                    "rationale": "관망",
                                },
                            ],
                        },
                        5,
                    )
                ],
            ),
        ]
    )
    run_id = await c.runner.run("manual")
    assert run_id is not None and route.call_count == 3
    # 두 번째 요청에는 도구 결과(role=tool)가 포함돼야 한다
    body = json.loads(route.calls[1].request.content)
    assert body["model"] == "test-model" and body["tools"]
    assert any(m["role"] == "tool" and m["tool_call_id"] == "call_1" for m in body["messages"])
    assert route.calls[0].request.headers["Authorization"] == "Bearer sk-test-123456"
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
        decs = {d.symbol: d for d in s.scalars(select(AgentDecision).where(AgentDecision.run_id == run_id))}
    assert run is not None and run.status == "DONE" and run.mode == "llm-tools"
    assert run.usage["total_tokens"] == 360
    assert decs["MOCK01"].order_id and decs["MOCK02"].order_id and decs["MOCK03"].action == "HOLD"
    o2 = next(o for o in _orders(c) if o.symbol == "MOCK02")
    assert o2.quantity * 182_000 < 1_100_000  # 1회 최대 주문금액으로 축소됨
    assert o2.status == OrderStatus.SUBMITTED.value and o2.fill_basis == "OPEN"
    chat = _chat(c)
    assert any(m.kind == "tool" and m.payload.get("tool") == "get_financials" for m in chat)
    assert any(m.role == "risk" and "축소" in m.content for m in chat)
    assert any(m.kind == "decision" and m.payload.get("action") == "BUY" for m in chat)
    assert "포트폴리오부터 볼게요 💼" in [m.content for m in chat]


@respx.mock
async def test_llm_without_tool_support_falls_back_to_json(tmp_path: Path) -> None:
    c = make(tmp_path, llm_base_url="https://llm.test/v1", llm_model="m", llm_api_key="k-123456")
    await _simulate(c, datetime(2026, 11, 2, 10, 0, tzinfo=KST))
    decision = {
        "thoughts": ["자료 잘 받았어요 📚", "가온전자가 괜찮아 보여요"],
        "market_view": "보합",
        "summary": "소폭 매수",
        "decisions": [
            {
                "symbol": "MOCK01",
                "action": "BUY",
                "quantity": 3,
                "confidence": 0.6,
                "rationale": "근거",
                "evidence": [],
            }
        ],
    }
    respx.post(LLM_URL).mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "tools is not supported for this model"}}),
            _resp("```json\n" + json.dumps(decision, ensure_ascii=False) + "\n```"),
        ]
    )
    run_id = await c.runner.run("manual")
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
    assert run is not None and run.status == "DONE" and run.mode == "llm-json"
    o = next(o for o in _orders(c) if o.symbol == "MOCK01")
    assert o.quantity == 3 and o.fill_basis == "CLOSE"
    assert "자료 잘 받았어요 📚" in [m.content for m in _chat(c)]


@respx.mock
async def test_llm_failure_marks_run_failed_without_orders(tmp_path: Path) -> None:
    c = make(tmp_path, llm_base_url="https://llm.test/v1", llm_model="m", llm_api_key="k-123456")
    await _simulate(c, datetime(2026, 11, 2, 10, 0, tzinfo=KST))
    n_before = len(_orders(c))
    respx.post(LLM_URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    run_id = await c.runner.run("manual")
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
    assert run is not None and run.status == "FAILED" and "401" in (run.error or "")
    assert len(_orders(c)) == n_before


@respx.mock
async def test_chat_answer_and_memo(tmp_path: Path) -> None:
    c = make(tmp_path, llm_base_url="https://llm.test/v1", llm_model="m", llm_api_key="k-123456")
    route = respx.post(LLM_URL).mock(return_value=_resp("아직 보유 종목이 없어서 관망 중이에요 🙂"))
    await c.runner.answer("요즘 왜 아무것도 안 사?")
    chat = _chat(c)
    assert chat[-2].role == "user" and chat[-1].role == "analyst"
    assert "관망" in chat[-1].content
    assert "요즘 왜 아무것도 안 사?" in json.loads(route.calls[0].request.content)["messages"][1]["content"]
    # 메모는 다음 세션 컨텍스트에 들어간다
    assert "요즘 왜 아무것도 안 사?" in c.runner._memory()


async def test_chat_without_llm(tmp_path: Path) -> None:
    c = make(tmp_path)
    await c.runner.answer("안녕?")
    assert "LLM" in _chat(c)[-1].content


async def test_unmentioned_holding_becomes_hold(tmp_path: Path) -> None:
    c = make(tmp_path)
    await _simulate(c, datetime(2026, 11, 2, 9, 30, tzinfo=KST))
    c.broker.state["positions"]["MOCK05"] = {"qty": 3, "avg_price": 100_000.0, "opened_day": "2026-10-01"}
    await c.runner._execute(1, {"summary": "s", "decisions": []})
    with c.db.session() as s:
        d = s.scalar(select(AgentDecision).where(AgentDecision.symbol == "MOCK05"))
    assert d is not None and d.action == "HOLD" and "언급하지 않아" in d.rationale


def test_sim_due_jobs_window(tmp_path: Path) -> None:
    c = make(tmp_path)
    jobs = c.scheduler.due_jobs(
        datetime(2026, 11, 2, 8, 30, tzinfo=KST), datetime(2026, 11, 2, 14, 0, tzinfo=KST)
    )
    assert [n for n, _ in jobs] == ["morning", "sync", "afternoon"]
    weekend = c.scheduler.due_jobs(
        datetime(2026, 10, 31, 0, 0, tzinfo=KST), datetime(2026, 11, 1, 23, 0, tzinfo=KST)
    )
    assert weekend == []
    assert c.scheduler.next_runs()[0]["name"] == "morning"


@pytest.mark.parametrize("step", [5, 30])
async def test_simulation_is_deterministic(tmp_path: Path, step: int) -> None:
    c1 = make(tmp_path / "a")
    c2 = make(tmp_path / "b")
    end = datetime(2026, 11, 3, 15, 0, tzinfo=KST)
    await _simulate(c1, end, step)
    await _simulate(c2, end, step)
    sig = lambda c: [(o.symbol, o.side, o.quantity, o.status) for o in _orders(c)]  # noqa: E731
    assert sig(c1) == sig(c2)
    assert (end - timedelta(minutes=step)) <= c1.clock.now()
