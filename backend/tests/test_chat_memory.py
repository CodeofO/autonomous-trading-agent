"""트레이딩 룸 대화: 매매 내역·실현손익 근거, 하루 단위 대화 유지(재시작 포함), 면책 문구 제거."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import respx
from sqlalchemy import select

from app.agent.runner import strip_disclaimers
from app.container import announce_boot
from app.db.models import AuditEvent, ChatMessage
from app.engine.events import USER
from app.risk.rules import OrderIntent
from tests.fake_toss import at
from tests.helpers import make_container

LLM_URL = "https://llm.test/v1/chat/completions"
D = date(2026, 11, 2)
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}


def _resp(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def _chat(c: Any) -> list[ChatMessage]:
    with c.db.session() as s:
        return list(s.scalars(select(ChatMessage).order_by(ChatMessage.id)))


async def _round_trip(c: Any) -> None:
    """005930 10주 매수 → 6주 매도 (토스 가짜 현재가로 즉시 체결)."""
    buy, _ = await c.engine.orders.submit(OrderIntent("005930", "BUY", 10, 70_000, "실적 개선"))
    c.clock.advance(timedelta(minutes=30))
    sell, _ = await c.engine.orders.submit(OrderIntent("005930", "SELL", 6, 70_000, "비중 축소"))
    assert buy is not None and sell is not None


async def test_trade_ledger_matches_broker_realized_pnl(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    await _round_trip(c)
    ledger = await c.runner.tb.call("get_trade_history", {})
    assert ledger["fill_count"] == 2
    assert ledger["realized_pnl_total"] == round(c.broker.state["realized_pnl"])
    sell = ledger["recent_fills"][0]  # 최신순
    assert sell["side"] == "SELL" and sell["quantity"] == 6 and sell["reason"] == "비중 축소"
    assert sell["realized_pnl"] == ledger["realized_pnl_total"] and "avg_cost" in sell


@respx.mock
async def test_chat_uses_trade_history_and_todays_conversation_across_restart(tmp_path: Path) -> None:
    db = f"sqlite:///{tmp_path}/app.db"
    c = make_container(tmp_path, at(D, 10), database_url=db, **LLM)
    await _round_trip(c)
    route = respx.post(LLM_URL).mock(
        side_effect=[_resp("삼성전자 6주 매도로 생긴 손익이에요"), _resp("네 맞아요")]
    )
    await c.runner.answer("실현손익은 어디서 났어?")

    # 재시작: 같은 DB 로 새 컨테이너
    c2 = make_container(tmp_path, at(D, 11), database_url=db, create_schema=False, **LLM)
    await c2.runner.answer("그럼 그건 네가 판 거지?")
    first = json.loads(route.calls[0].request.content)["messages"]
    second = json.loads(route.calls[1].request.content)["messages"]
    assert "realized_pnl_total" in first[0]["content"] and "비중 축소" in first[0]["content"]
    assert [m["role"] for m in second[1:]] == ["user", "assistant", "user"]  # 오늘 대화가 이어짐
    assert second[1]["content"] == "실현손익은 어디서 났어?" and "6주 매도" in second[2]["content"]
    assert second[-1]["content"] == "그럼 그건 네가 판 거지?"


@respx.mock
async def test_yesterdays_conversation_is_not_carried_over(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    route = respx.post(LLM_URL).mock(side_effect=[_resp("어제 답"), _resp("오늘 답")])
    await c.runner.answer("어제 질문")
    c.clock.advance(timedelta(days=1))
    await c.runner.answer("오늘 질문")
    msgs = json.loads(route.calls[1].request.content)["messages"]
    assert [m["content"] for m in msgs[1:]] == ["오늘 질문"]


@respx.mock
async def test_restart_answers_pending_question_once(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    route = respx.post(LLM_URL).mock(return_value=_resp("늦었지만 답할게요"))
    c.hub.chat(USER, "text", "답 못 받은 질문")  # 재시작 직전에 들어온 질문
    await c.runner.resume_pending()
    assert route.call_count == 1 and _chat(c)[-1].content == "늦었지만 답할게요"
    await c.runner.resume_pending()  # 이미 답했으면 다시 답하지 않음
    assert route.call_count == 1


def test_boot_notice_once_per_day(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 8))
    assert announce_boot(c, "🟢 시스템 기동") is True
    c.clock.advance(timedelta(hours=2))
    assert announce_boot(c, "🟢 시스템 기동") is False  # 같은 날 재시작 → 채팅에 안 띄움
    c.clock.advance(timedelta(days=1))
    assert announce_boot(c, "🟢 시스템 기동") is True
    assert sum("시스템 기동" in m.content for m in _chat(c)) == 2
    with c.db.session() as s:
        assert s.scalar(select(AuditEvent).where(AuditEvent.message.like("재시작%"))) is not None


@respx.mock
async def test_disclaimer_sentences_are_removed(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10), **LLM)
    respx.post(LLM_URL).mock(return_value=_resp("현금은 250만원이에요. 모의투자이고 투자 권유는 아니에요."))
    await c.runner.answer("현금 얼마야?")
    assert _chat(c)[-1].content == "현금은 250만원이에요."


def test_strip_disclaimers_keeps_other_lines() -> None:
    text = "첫 줄이에요.\n이건 투자 권유가 아닙니다.\n모의투자 계좌 기준 현금은 250만원이에요."
    assert strip_disclaimers(text) == "첫 줄이에요.\n모의투자 계좌 기준 현금은 250만원이에요."
    assert strip_disclaimers("그냥 답변이에요") == "그냥 답변이에요"
