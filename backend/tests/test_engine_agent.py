"""엔진·에이전트 통합 테스트 (테스트 전용 가짜 토스 + 규칙 에이전트 / respx 로 가짜 LLM)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.agent.tools import openai_tools
from app.brokers.base import OrderStatus
from app.core.clock import KST
from app.db.models import AgentDecision, AgentRun, ChatMessage, DailyReport, Fill, Order
from app.engine.scheduler import daily_jobs, session_times
from app.market.toss import TossApiError
from tests.fake_toss import at
from tests.helpers import make_container

LLM_URL = "https://llm.test/v1/chat/completions"
D = date(2026, 11, 2)  # 월요일
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}


def make(tmp_path: Path, start: datetime | None = None, **kw: Any) -> Any:
    return make_container(tmp_path, start or at(D, 10), **kw)


def _chat(c: Any) -> list[ChatMessage]:
    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage).order_by(ChatMessage.id)))


def _orders(c: Any) -> list[Order]:
    with c.db.session() as s:
        return list(s.scalars(select(Order).order_by(Order.id)))


# ------------------------------------------------------------------ 하루 운영 (규칙 에이전트)
async def test_trading_day_with_rule_agent(tmp_path: Path) -> None:
    c = make(tmp_path, at(D, 8, 35))
    await c.engine.morning()  # 장전 판단 → 09:00 이후 체결 예정
    pre = _orders(c)
    assert pre and all(o.status == "SUBMITTED" and o.fill_basis == "OPEN" for o in pre)
    c.clock.set(at(D, 9, 1))
    await c.engine.intraday()  # 장 시작 → 현재가 체결
    assert all(o.status == "FILLED" for o in _orders(c))
    c.clock.set(at(D, 10))
    await c.engine.hourly()
    c.clock.set(at(D, 15, 21))
    await c.engine.intraday()
    from app.reports.daily import build_daily_report

    c.clock.set(at(D, 15, 45))
    rep = await build_daily_report(c.engine)
    with c.db.session() as s:
        runs = list(s.scalars(select(AgentRun)))
        fills = list(s.scalars(select(Fill)))
        reports = list(s.scalars(select(DailyReport)))
    assert [r.trigger for r in runs] == ["morning", "hourly"] and all(r.mode == "rule" for r in runs)
    assert fills and len(reports) == 1 and rep["positions"]
    assert {"analyst", "broker", "system"} <= {m.role for m in _chat(c)}
    assert {"tool", "decision", "summary", "fill"} <= {m.kind for m in _chat(c)}
    assert all(o.status in {s.value for s in OrderStatus} for o in _orders(c))


async def test_kill_switch_cancels_and_blocks(tmp_path: Path) -> None:
    c = make(tmp_path, at(D, 8, 40))
    await c.engine.morning()
    open_before = c.engine.open_order_count()
    assert open_before > 0
    assert await c.engine.kill("테스트") == open_before and c.engine.open_order_count() == 0
    assert c.engine.status == "KILLED"
    assert await c.engine.run_agent("manual") is None
    from app.risk.rules import OrderIntent

    order, decision = await c.engine.orders.submit(OrderIntent("005930", "BUY", 1, 70_000))
    assert order is None and decision.rule == "kill_switch"
    c.engine.resume()
    assert c.engine.status == "RUNNING"
    assert "킬 스위치" in " ".join(m.content for m in _chat(c))


async def test_toss_unavailable_skips_session_with_warning(tmp_path: Path) -> None:
    c = make(tmp_path)
    c.toss.client.fail = TossApiError(403, "access_denied", "IP address not allowed")
    assert await c.engine.run_agent("manual") is None
    assert any("허용 IP" in m.content for m in _chat(c))
    with c.db.session() as s:
        assert list(s.scalars(select(AgentRun))) == []


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
async def test_llm_tool_loop_uses_toss_tools_and_places_orders(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    route = respx.post(LLM_URL).mock(
        side_effect=[
            _resp("시장 흐름부터 볼게요 🌐", [_tc("get_market_overview", {}, 1)]),
            _resp(
                "삼성전자 수급과 상태를 볼게요",
                [
                    _tc("get_supply_demand", {"symbol": "005930"}, 2),
                    _tc("get_stock_status", {"symbol": "005930"}, 3),
                    _tc("get_price_history", {"symbol": "005930", "days": 60}, 4),
                    _tc("explore_sector", {"query": "반도체"}, 5),
                    _tc("screen_stocks", {"sort_by": "foreigner_net_buy"}, 6),
                ],
            ),
            _resp(
                None,
                [
                    _tc(
                        "submit_decisions",
                        {
                            "market_view": "중립",
                            "summary": "삼성전자 담고 하이닉스는 크게 사볼게요!",
                            "decisions": [
                                {
                                    "symbol": "005930",
                                    "action": "BUY",
                                    "target_weight": 0.08,
                                    "confidence": 0.7,
                                    "rationale": "외국인 순매수",
                                },
                                {
                                    "symbol": "000660",
                                    "action": "BUY",
                                    "quantity": 100000,
                                    "confidence": 0.9,
                                    "rationale": "과감히",
                                },
                                {
                                    "symbol": "035420",
                                    "action": "HOLD",
                                    "confidence": 0.3,
                                    "rationale": "관망",
                                },
                            ],
                        },
                        7,
                    )
                ],
            ),
        ]
    )
    run_id = await c.runner.run("manual")
    assert run_id is not None and route.call_count == 3
    body = json.loads(route.calls[0].request.content)
    tool_names = {t["function"]["name"] for t in body["tools"]}
    assert {"get_market_overview", "get_supply_demand", "get_stock_status", "explore_sector"} <= tool_names
    msgs = json.loads(route.calls[2].request.content)["messages"]
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in msgs if m["role"] == "tool"}
    assert "KOSPI" in results["call_1"]["indices"] and results["call_1"]["usd_krw"]["rate"] == 1380.5
    assert results["call_2"]["investor_net_buy_daily"] and results["call_3"]["orderbook_top5"]["asks"]
    assert results["call_4"]["realtime_price"] > 0 and results["call_5"]["sector"]["name"] == "반도체"
    assert results["call_6"]["results"][0]["symbol"] == "005930"
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
        decs = {d.symbol: d for d in s.scalars(select(AgentDecision).where(AgentDecision.run_id == run_id))}
    assert run is not None and run.status == "DONE" and run.usage["total_tokens"] == 360
    assert decs["005930"].order_id and decs["000660"].order_id and decs["035420"].action == "HOLD"
    o2 = next(o for o in _orders(c) if o.symbol == "000660")
    assert o2.status == "FILLED" and o2.fill_basis == "NOW"  # 장중 현재가 즉시 체결
    assert (
        o2.quantity * c.toss.client.last_price("000660") < 2_100_000
    )  # 1회 최대 주문금액(200만 원)으로 축소
    chat = _chat(c)
    assert any(m.kind == "tool" and m.payload.get("tool") == "get_supply_demand" for m in chat)
    assert any(m.role == "risk" and "축소" in m.content for m in chat)
    assert any(m.kind == "fill" for m in chat)


@respx.mock
async def test_llm_without_tool_support_falls_back_to_json(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    decision = {
        "thoughts": ["자료 잘 받았어요 📚"],
        "market_view": "보합",
        "summary": "소폭 매수",
        "decisions": [
            {"symbol": "삼성전자", "action": "BUY", "quantity": 3, "confidence": 0.6, "rationale": "r"}
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
    o = next(o for o in _orders(c) if o.symbol == "005930")
    assert o.quantity == 3 and o.status == "FILLED"


@respx.mock
async def test_llm_failure_marks_run_failed_without_orders(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    respx.post(LLM_URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    run_id = await c.runner.run("manual")
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
    assert run is not None and run.status == "FAILED" and "401" in (run.error or "")
    assert _orders(c) == []


@respx.mock
async def test_llm_decisions_as_json_string_are_executed(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    decisions = json.dumps(
        [{"symbol": "삼성전자", "action": "BUY", "quantity": 2, "confidence": 0.6, "rationale": "r"}],
        ensure_ascii=False,
    )
    respx.post(LLM_URL).mock(
        return_value=_resp(None, [_tc("submit_decisions", {"summary": "s", "decisions": decisions}, 1)])
    )
    run_id = await c.runner.run("manual")
    with c.db.session() as s:
        d = s.scalar(select(AgentDecision).where(AgentDecision.run_id == run_id))
    assert d is not None and d.symbol == "005930" and d.action == "BUY" and d.order_id is not None


@respx.mock
async def test_llm_forced_submit_after_ignoring_nudge(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    route = respx.post(LLM_URL).mock(
        side_effect=[
            _resp("분석해 보니 관망이 좋겠어요."),
            _resp("네, 관망입니다."),
            _resp(None, [_tc("submit_decisions", {"summary": "관망", "decisions": []}, 1)]),
        ]
    )
    run_id = await c.runner.run("manual")
    with c.db.session() as s:
        run = s.get(AgentRun, run_id)
    assert run is not None and run.status == "DONE"
    assert json.loads(route.calls[2].request.content)["tool_choice"]["function"]["name"] == "submit_decisions"


@respx.mock
async def test_chat_answer_and_memo(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    route = respx.post(LLM_URL).mock(return_value=_resp("아직 보유 종목이 없어서 관망 중이에요 🙂"))
    await c.runner.answer("요즘 왜 아무것도 안 사?")
    chat = _chat(c)
    assert chat[-2].role == "user" and chat[-1].role == "analyst" and "관망" in chat[-1].content
    assert "요즘 왜 아무것도 안 사?" in json.loads(route.calls[0].request.content)["messages"][1]["content"]
    # D-67: 보낸 말이 그대로 기억에 들어가지 않는다 — 판단 전 정리(LLM)를 거쳐 되풀이한 주제만 남는다
    assert "요즘 왜 아무것도 안 사?" not in c.runner._memory()


async def test_chat_without_llm(tmp_path: Path) -> None:
    c = make(tmp_path)
    await c.runner.answer("안녕?")
    assert "LLM" in _chat(c)[-1].content


async def test_unmentioned_holding_becomes_hold(tmp_path: Path) -> None:
    c = make(tmp_path)
    c.broker.state["positions"]["005380"] = {"qty": 3, "avg_price": 230_000.0, "opened_day": "2026-10-01"}
    with c.db.session() as s:
        run = AgentRun(trigger="manual", mode="llm-tools", status="RUNNING", started_at=c.clock.now())
        s.add(run)
        s.flush()
        run_id = run.id
    await c.runner._execute(run_id, {"summary": "s", "decisions": []})
    with c.db.session() as s:
        d = s.scalar(select(AgentDecision).where(AgentDecision.symbol == "005380"))
    assert d is not None and d.action == "HOLD" and "언급하지 않아" in d.rationale


# ------------------------------------------------------------------ 일정·도구 목록·유틸
def test_hourly_session_times(tmp_path: Path) -> None:
    from datetime import time

    assert session_times(60) == [time(h) for h in range(10, 16)]
    assert session_times(30)[0] == time(9, 30) and session_times(30)[-1] == time(15, 0)
    assert session_times(0) == [time(14, 0)]
    c = make(tmp_path, at(D, 8, 0))
    names = [n for _, n, _ in daily_jobs(c.engine, 60)]
    assert names == ["morning"] + ["hourly"] * 6 + ["report"]
    assert c.scheduler.next_runs()[0]["name"] == "morning"
    sat = make(tmp_path / "s", datetime(2026, 11, 7, 12, 0, tzinfo=KST))
    assert sat.scheduler.next_runs()[0]["at"].startswith("2026-11-09T08:35")


def test_toss_tools_only_when_connected() -> None:
    plain = {t["function"]["name"] for t in openai_tools(toss=False)}
    full = [t["function"]["name"] for t in openai_tools(toss=True)]
    assert "get_market_overview" not in plain and "get_market_overview" in full
    assert full[-1] == "submit_decisions"


def test_strip_reasoning_blocks() -> None:
    from app.agent.llm import strip_reasoning

    assert strip_reasoning("<think>내부 추론</think>포트폴리오부터 볼게요") == "포트폴리오부터 볼게요"
    assert strip_reasoning("앞<THINK>a\nb</THINK> 뒤") == "앞 뒤"
    assert strip_reasoning("<think>끝나지 않은 추론") == ""


def test_normalize_decisions_formats() -> None:
    from app.agent.runner import normalize_decisions

    lookup = {"005930": "005930", "삼성전자": "005930", "005380": "005380", "현대차": "005380"}
    as_str = json.dumps([{"symbol": "005380", "action": "SELL", "target_weight": 0}])
    assert normalize_decisions(as_str, lookup)[0]["action"] == "SELL"
    assert normalize_decisions({"005930": {"action": "hold", "confidence": 0.5}}, lookup) == [
        {"action": "HOLD", "confidence": 0.5, "symbol": "005930"}
    ]
    named = [
        {"ticker": "삼성전자(005930)", "decision": "매수"},
        {"name": "현대차", "action": "매도"},
        "junk",
        3,
    ]
    got = normalize_decisions(named, lookup)
    assert [(g["symbol"], g["action"]) for g in got] == [("005930", "BUY"), ("005380", "SELL")]
    assert normalize_decisions(None, lookup) == [] and normalize_decisions("not json", lookup) == []


async def test_financials_valuation_uses_toss_market_cap(tmp_path: Path) -> None:
    c = make(tmp_path)

    async def fake_fin(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "available": True,
            "source": "DART",
            "reports": [
                {
                    "period": "2025 사업보고서",
                    "type": "A",
                    "fs_div": "CFS",
                    "revenue": 3e14,
                    "operating_income": 4e13,
                    "net_income": 4.5e13,
                    "total_equity": 4e14,
                    "total_liabilities": 1.2e14,
                    "prior": {},
                }
            ],
        }

    c.research.financials = fake_fin  # type: ignore[method-assign]
    res = await c.runner.tb.call("get_financials", {"symbol": "005930"})
    mcap = c.toss.client.last_price("005930") * 5.9e9
    assert res["valuation"]["market_cap_eok"] == round(mcap / 1e8, 1)
    assert res["valuation"]["per"] == round(mcap / 4.5e13, 1) and res["valuation"]["pbr"] is not None


@respx.mock
async def test_submission_without_decisions_is_bounced_once(tmp_path: Path) -> None:
    c = make(tmp_path, **LLM)
    route = respx.post(LLM_URL).mock(
        side_effect=[
            _resp(
                None,
                [
                    _tc(
                        "submit_decisions",
                        {"market_view": "회복 초기", "summary": "삼성전자를 소량 담았어요"},
                        1,
                    )
                ],
            ),
            _resp(
                None,
                [
                    _tc(
                        "submit_decisions",
                        {
                            "summary": "삼성전자 매수",
                            "decisions": [
                                {
                                    "symbol": "005930",
                                    "action": "BUY",
                                    "quantity": 2,
                                    "confidence": 0.6,
                                    "rationale": "r",
                                }
                            ],
                        },
                        2,
                    )
                ],
            ),
        ]
    )
    run_id = await c.runner.run("manual")
    assert route.call_count == 2
    second = json.loads(route.calls[1].request.content)
    assert second["tool_choice"]["function"]["name"] == "submit_decisions"
    assert any(m["role"] == "tool" and "decisions" in m["content"] for m in second["messages"])
    with c.db.session() as s:
        d = s.scalar(select(AgentDecision).where(AgentDecision.run_id == run_id))
    assert d is not None and d.symbol == "005930" and d.action == "BUY" and d.order_id is not None
    assert any("다시 요청" in m.content for m in _chat(c))
