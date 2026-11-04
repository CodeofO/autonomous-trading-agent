"""에이전트 세션 실행기: LLM 도구 루프(또는 JSON 모드/규칙 모드) → 결정 → 리스크 → 주문.

진행 과정은 EventHub 채팅으로 실시간 표시된다(트레이딩 룸).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.agent.activity import preview, result_line, tool_source
from app.agent.costs import record_usage
from app.agent.llm import LLMClient, LLMError, ToolsUnsupportedError, extract_json
from app.agent.prompts import (
    ACCOUNT_DESC,
    CHAT_SYSTEM_PROMPT,
    DEFAULT_PERSONA,
    DEFAULT_POLICY,
    JSON_MODE_INSTRUCTIONS,
    SYSTEM_PROMPT,
)
from app.agent.rule_agent import rule_based_decisions
from app.agent.tools import TERMINAL_TOOL, ToolBox, openai_tools, tool_label
from app.brokers.base import OrderType
from app.core.clock import KST, Clock
from app.db.models import AgentDecision, AgentRun, ChatMessage
from app.db.session import Database
from app.engine.events import ANALYST, SYSTEM, USER, EventHub
from app.engine.orders import OrderManager
from app.engine.settings_store import SettingsStore
from app.risk.rules import OrderIntent
from app.strategies.rules import Strategy

log = logging.getLogger(__name__)

TRIGGER_KO = {
    "morning": "☀️ 장 시작 전 점검",
    "hourly": "⏰ 정시 점검",
    "afternoon": "🌤️ 오후 점검",
    "manual": "👆 수동 실행",
}


class AgentRunner:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        hub: EventHub,
        toolbox: ToolBox,
        orders: OrderManager,
        settings: SettingsStore,
        llm: LLMClient | None,
        *,
        tool_mode: str = "auto",
        max_steps: int = 24,
        strategies: Callable[[], list[Strategy]] | None = None,
        names: Callable[[str], str] | None = None,
        live: bool = False,
    ) -> None:
        self.live = live  # 실거래 모드 (프롬프트의 계좌 설명이 달라진다)
        self.db = db
        self.clock = clock
        self.hub = hub
        self.tb = toolbox
        self.orders = orders
        self.settings = settings
        self.llm = llm
        self.tool_mode = tool_mode
        self.max_steps = max_steps
        self.strategies = strategies or (lambda: [])
        self.names = names or (lambda s: s)
        self._lock = asyncio.Lock()
        self._reply_lock = asyncio.Lock()
        self._handled_question = 0  # 마지막으로 답하기 시작한 질문 id (같은 질문에 두 번 답하지 않음)
        self._typing_slots: dict[str, str | None] = {"session": None, "reply": None}
        self._run: dict[str, Any] = {"active": False}

    @property
    def running(self) -> bool:
        return self._lock.locked()

    # ================================================================ 활동 표시
    @property
    def progress(self) -> dict[str, Any]:
        """지금 하는 일: 판단 세션 진행 상황 + '생각 중…' 표시. 새로고침·재접속 때 화면을 복원하는 데 쓴다."""
        return {**self._run, "typing": self._typing_label(), "replying": self._reply_lock.locked()}

    def _typing_label(self) -> str | None:
        return self._typing_slots["reply"] or self._typing_slots["session"]

    def _typing(self, slot: str, label: str | None) -> None:
        """세션과 답장이 겹쳐도 한쪽이 끝날 때 다른 쪽 표시를 지우지 않는다."""
        self._typing_slots[slot] = label
        cur = self._typing_label()
        self.hub.typing(ANALYST, cur is not None, cur or "")
        self.hub.progress(self.progress)

    def _step(self, label: str, **changes: Any) -> None:
        """세션 진행 단계 갱신 → 트레이딩 룸 활동 패널·'생각 중' 표시."""
        self._run.update(changes, label=label)
        self._typing("session", label)

    async def _tool(
        self, run_id: int, name: str, args: dict[str, Any], step: int | None = None
    ) -> dict[str, Any]:
        """도구 실행 + 채팅 표시 (데이터 출처·소요 시간·결과 한 줄 요약)."""
        label = tool_label(name, args, self.names)
        source = tool_source(name, self.live)
        self._step(label, phase="tool", tool=name, source=source)
        t0 = time.monotonic()
        result = await self.tb.call(name, args)
        ms = round((time.monotonic() - t0) * 1000)
        sources = dict(self._run.get("sources") or {})
        sources[source] = sources.get(source, 0) + 1
        self._run.update(tools=int(self._run.get("tools") or 0) + 1, sources=sources)
        self.hub.chat(
            ANALYST,
            "tool",
            label,
            run_id=run_id,
            tool=name,
            args=args,
            source=source,
            ms=ms,
            step=step,
            ok="error" not in result,
            line=result_line(name, result),
            preview=preview(name, result),
        )
        return result

    def persona(self) -> dict[str, str]:
        return {**DEFAULT_PERSONA, **self.settings.get("agent_persona")}

    def policy(self) -> str:
        return str(self.settings.get("agent_policy").get("text") or DEFAULT_POLICY)

    # ================================================================ 세션
    async def run(self, trigger: str = "manual") -> int | None:
        if self._lock.locked():
            self.hub.chat(SYSTEM, "text", "⏳ 이미 판단 세션이 진행 중이에요. 끝나면 다시 시도해 주세요.")
            return None
        async with self._lock:
            mode = "rule" if self.llm is None else ("llm-json" if self.tool_mode == "json" else "llm-tools")
            with self.db.session() as s:
                run = AgentRun(
                    trigger=trigger,
                    mode=mode,
                    model=self.llm.model if self.llm else "rule-based",
                    status="RUNNING",
                    started_at=self.clock.now(),
                )
                s.add(run)
                s.flush()
                run_id = run.id
            p = self.persona()
            self.hub.chat(
                SYSTEM,
                "text",
                f"{TRIGGER_KO.get(trigger, trigger)} — {p['emoji']} {p['name']} 매니저가 "
                f"출근했어요. (세션 #{run_id})",
                run_id=run_id,
                trigger=trigger,
            )
            self.hub.state({"agent_running": True, "run_id": run_id})
            self._run = {
                "active": True,
                "run_id": run_id,
                "trigger": trigger,
                "mode": mode,
                "started_at": self.clock.now().isoformat(),
                "step": 0,
                "max_steps": self.max_steps,
                "tools": 0,
                "sources": {},
                "tokens": 0,
            }
            self._step("출근 준비 중…", phase="start")
            usage: dict[str, int] = {}
            try:
                if self.llm is None:
                    payload = await self._run_rules(run_id)
                elif mode == "llm-json":
                    payload = await self._run_json(run_id, usage)
                else:
                    try:
                        payload = await self._run_tools(run_id, usage)
                    except ToolsUnsupportedError as e:
                        if self.tool_mode != "auto":
                            raise
                        self.hub.chat(
                            SYSTEM,
                            "text",
                            "🔁 이 LLM 은 도구 호출을 지원하지 않아서 자료를 미리 모아 "
                            "전달하는 방식으로 바꿀게요.",
                            run_id=run_id,
                            detail=str(e)[:200],
                        )
                        mode = "llm-json"
                        payload = await self._run_json(run_id, usage)
                await self._execute(run_id, payload)
                status, err = "DONE", None
            except Exception as e:
                log.exception("에이전트 세션 실패")
                status, err = "FAILED", f"{type(e).__name__}: {str(e)[:300]}"
                self.hub.chat(
                    SYSTEM,
                    "text",
                    f"⚠️ 세션 #{run_id} 중 문제가 생겼어요: {err}. 이번 세션 주문은 내지 않았어요.",
                    run_id=run_id,
                )
            finally:
                self._run = {"active": False, "run_id": run_id}
                self._typing("session", None)
                self.hub.state({"agent_running": False, "run_id": run_id})
            with self.db.session() as s:
                done = s.get(AgentRun, run_id)
                assert done is not None
                done.status, done.error, done.mode = status, err, mode
                done.finished_at = self.clock.now()
                done.usage = usage
            self.hub.audit(
                "AGENT_RUN", f"세션 #{run_id} {status}", None, run_id=run_id, mode=mode, usage=usage
            )
            return run_id

    # ================================================================ 컨텍스트
    def _memory(self) -> str:
        since_raw = self.settings.get("account_source").get("since")
        with self.db.session() as s:
            q = select(AgentRun).where(AgentRun.status == "DONE")
            if since_raw:  # 계좌를 새로 시작한 이후~현재 기록만 (예전 가상 시장·미래 날짜 기록 차단)
                q = q.where(
                    AgentRun.started_at >= datetime.fromisoformat(since_raw),
                    AgentRun.started_at <= self.clock.now(),
                )
            runs = list(s.scalars(q.order_by(AgentRun.id.desc()).limit(3)))
            lines = []
            for r in runs:
                decs = list(s.scalars(select(AgentDecision).where(AgentDecision.run_id == r.id)))
                acts = (
                    ", ".join(f"{d.name or d.symbol} {d.action}" for d in decs if d.action != "HOLD")
                    or "거래 없음"
                )
                lines.append(f"- 세션#{r.id} ({_kst(r.started_at)}): {r.summary[:160]} / {acts}")
            memos = list(
                s.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.role == USER)
                    .order_by(ChatMessage.id.desc())
                    .limit(5)
                )
            )
        memo_txt = "\n".join(f"- {_kst(m.ts)}: {m.content[:200]}" for m in reversed(memos)) or "- 없음"
        return (
            "최근 판단 기록:\n"
            + ("\n".join(lines) or "- 없음")
            + "\n\n투자자 메모(참고용 의견이며 리스크 규칙보다 우선하지 않음):\n"
            + memo_txt
        )

    def _system(self) -> str:
        p = self.persona()
        return SYSTEM_PROMPT.format(
            name=p["name"], emoji=p["emoji"], policy=self.policy(), account=ACCOUNT_DESC[self.live]
        )

    def _user_context(self, trigger: str) -> str:
        now = self.clock.now()
        return (
            f"현재 시각: {now.astimezone(KST):%Y-%m-%d %H:%M} (KST)\n세션: {TRIGGER_KO.get(trigger, trigger)}\n\n"
            f"{self._memory()}\n\n지금부터 점검을 시작해 줘. 보유 종목 재평가와 신규 후보 탐색을 모두 하고, "
            f"마지막에 submit_decisions 로 제출해."
        )

    # ================================================================ 모드: 도구
    async def _run_tools(self, run_id: int, usage: dict[str, int]) -> dict[str, Any]:
        assert self.llm is not None
        with self.db.session() as s:
            r = s.get(AgentRun, run_id)
            trigger = r.trigger if r else "manual"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system()},
            {"role": "user", "content": self._user_context(trigger)},
        ]
        tools = openai_tools(toss=self.tb.toss is not None)
        nudged = False
        force_submit = False
        resubmitted = False
        prev_input = 0  # 바로 전 호출 입력 — 다음 호출 앞부분과 겹친다(캐시 적중 추정, D-41)
        for step in range(self.max_steps):
            force = force_submit or step == self.max_steps - 1
            self._step("생각 중…", phase="thinking", step=step + 1, tool=None, source=None)
            res = await self.llm.chat(
                messages,
                tools,
                {"type": "function", "function": {"name": TERMINAL_TOOL}} if force else None,
            )
            self._log_usage("session", run_id, res.usage, prev_input)
            prev_input = int(res.usage.get("prompt_tokens") or 0)
            _acc(usage, res.usage)
            self._run["tokens"] = usage.get("total_tokens", 0)
            if res.content:
                self.hub.chat(ANALYST, "text", res.content[:1500], run_id=run_id)
            if not res.tool_calls:
                maybe = extract_json(res.content) if res.content else None
                if maybe and "decisions" in maybe:
                    return maybe
                if force:
                    raise LLMError("모델이 submit_decisions 를 호출하지 않았습니다")
                if nudged:
                    force_submit = True  # 재촉에도 응하지 않으면 다음 요청에서 제출 도구를 강제
                    continue
                nudged = True
                messages.append({"role": "assistant", "content": res.content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": "좋아요. 이제 submit_decisions 도구로 최종 판단을 제출해 주세요.",
                    }
                )
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": res.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False),
                            },
                        }
                        for c in res.tool_calls
                    ],
                }
            )
            if res.finish_reason == "length":
                log.warning("LLM 응답이 길이 한도로 잘렸습니다 (LLM_MAX_TOKENS 상향 권장)")
            for call in res.tool_calls:
                if call.name == TERMINAL_TOOL:
                    if "decisions" in call.arguments or resubmitted:
                        return call.arguments
                    # 결정 목록이 빠진 제출(잘림·누락) → 한 번 돌려보내 다시 받는다
                    resubmitted = True
                    self.hub.chat(
                        SYSTEM,
                        "text",
                        "📝 결정 목록이 빠진 제출이라 매니저에게 다시 요청했어요.",
                        run_id=run_id,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {
                                    "error": "decisions 배열이 비어 있습니다. 요약에 적은 매수·매도와 보유 종목 전부를 decisions "
                                    "배열에 넣어 submit_decisions 를 다시 호출하세요. 요약은 짧게."
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    force_submit = True
                    continue  # 같은 묶음의 다른 도구 호출에도 응답해야 하므로 계속 진행
                result = await self._tool(run_id, call.name, call.arguments, step=step + 1)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": self.tb.dumps(result)})
            if step == self.max_steps - 2:
                messages.append(
                    {
                        "role": "user",
                        "content": "도구 호출 한도에 가까워요. 지금까지 정보로 submit_decisions 를 제출해 주세요.",
                    }
                )
        raise LLMError("최대 단계 안에 결론을 내지 못했습니다")

    # ================================================================ 모드: JSON
    async def _run_json(self, run_id: int, usage: dict[str, int]) -> dict[str, Any]:
        assert self.llm is not None
        dossier: dict[str, Any] = {}

        async def gather(name: str, args: dict[str, Any]) -> dict[str, Any]:
            return await self._tool(run_id, name, args)

        dossier["portfolio"] = await gather("get_portfolio", {})
        scr = await gather("screen_stocks", {"sort_by": "market_cap", "limit": 12})
        dossier["screen"] = scr
        syms = [p["symbol"] for p in dossier["portfolio"].get("positions", [])]
        syms += [r["symbol"] for r in scr.get("results", []) if r["symbol"] not in syms][
            : max(0, 10 - len(syms))
        ]
        dossier["stocks"] = {}
        for sym in syms:
            name = self.names(sym) or sym
            dossier["stocks"][sym] = {
                "price": await gather("get_price_history", {"symbol": sym, "days": 60}),
                "financials": await gather("get_financials", {"symbol": sym}),
                "news": await gather("search_news", {"query": name, "limit": 4}),
            }
        self._step("자료 종합 중…", phase="thinking", tool=None, source=None)
        content = (
            self._user_context("json")
            + "\n\n"
            + JSON_MODE_INSTRUCTIONS
            + "\n자료:\n"
            + json.dumps(dossier, ensure_ascii=False, default=str)[:60000]
        )
        res = await self.llm.chat(
            [{"role": "system", "content": self._system()}, {"role": "user", "content": content}]
        )
        self._log_usage("session", run_id, res.usage)
        _acc(usage, res.usage)
        self._run["tokens"] = usage.get("total_tokens", 0)
        data = extract_json(res.content)
        if not data or "decisions" not in data:
            raise LLMError("JSON 결정을 해석하지 못했습니다")
        for t in (data.get("thoughts") or [])[:8]:
            self.hub.chat(ANALYST, "text", str(t)[:500], run_id=run_id)
        return data

    # ================================================================ 모드: 규칙
    async def _run_rules(self, run_id: int) -> dict[str, Any]:
        async def say(kind: str, text: str, payload: dict[str, Any]) -> None:
            self._step(text if kind == "tool" else "생각 중…", phase="tool" if kind == "tool" else "thinking")
            self.hub.chat(ANALYST, kind, text, run_id=run_id, **payload)

        return await rule_based_decisions(self.tb, self.strategies(), say)

    # ================================================================ 실행
    async def _execute(self, run_id: int, payload: dict[str, Any]) -> None:
        self._step("리스크 점검·주문 처리 중…", phase="executing", tool=None, source=None)
        pf = await self.tb.call("get_portfolio", {})
        equity = float(pf.get("equity") or 0)
        held = {p["symbol"]: p for p in pf.get("positions", [])}
        self.hub.audit(
            "AGENT_PAYLOAD",
            "최종 결정 원본",
            None,
            run_id=run_id,
            payload=json.dumps(payload, ensure_ascii=False, default=str)[:4000],
        )
        raw = normalize_decisions(payload.get("decisions"), await self._symbol_lookup(held))
        decisions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for d in raw:
            sym = d["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            decisions.append({**d, "symbol": sym, "action": str(d.get("action", "HOLD")).upper()})
        for sym in held:  # 언급 안 된 보유 종목은 유지 처리
            if sym not in seen:
                decisions.append(
                    {
                        "symbol": sym,
                        "action": "HOLD",
                        "confidence": 0.0,
                        "rationale": "에이전트가 이 종목을 언급하지 않아 시스템이 '유지'로 처리했어요.",
                        "evidence": [],
                    }
                )
        with self.db.session() as s:
            run = s.get(AgentRun, run_id)
            assert run is not None
            run.summary = str(payload.get("summary") or "")[:2000]
            run.market_view = str(payload.get("market_view") or "")[:2000]
        if payload.get("market_view"):
            self.hub.chat(ANALYST, "text", f"🌏 시장 관점: {payload['market_view']}"[:800], run_id=run_id)

        order_rank = {"SELL": 0, "BUY": 1, "HOLD": 2}
        decisions.sort(key=lambda d: order_rank.get(d["action"], 3))
        for d in decisions:
            await self._execute_one(run_id, d, equity, held)
        self.hub.chat(
            ANALYST,
            "summary",
            strip_disclaimers(str(payload.get("summary") or ""))[:1500] or "오늘 점검 끝!",
            run_id=run_id,
            counts={a: sum(x["action"] == a for x in decisions) for a in ("BUY", "SELL", "HOLD")},
        )

    async def _symbol_lookup(self, held: dict[str, Any]) -> dict[str, str]:
        """종목명/코드 → 종목코드 (LLM 이 이름으로 답하는 경우 대비). 토스 전체 종목 목록 우선."""
        out: dict[str, str] = {}
        if self.tb.toss is not None:
            try:
                for sym, it in (await self.tb.toss.universe()).items():
                    out[sym.upper()] = sym
                    if it.get("name"):
                        out[str(it["name"])] = sym
            except Exception as e:
                log.warning("종목 목록 조회 실패(이름→코드 변환): %s", e)
        for b in self.tb.store.snapshot():
            out[b.symbol.upper()] = b.symbol
            if b.name:
                out[b.name] = b.symbol
        for sym, p in held.items():
            out[sym.upper()] = sym
            if p.get("name"):
                out[str(p["name"])] = sym
        return out

    async def _execute_one(self, run_id: int, d: dict[str, Any], equity: float, held: dict[str, Any]) -> None:
        sym, action = d["symbol"], d["action"]
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        name = self.names(sym) or held.get(sym, {}).get("name", "")
        quote = await self.orders.broker.get_quote(sym)
        price = quote.price if quote else 0.0
        cur_qty = int(held.get(sym, {}).get("quantity", 0))
        qty = _to_int(d.get("quantity"))
        tw = _to_float(d.get("target_weight"))
        if action == "BUY" and not qty and tw is not None and price > 0:
            qty = max(0, math.floor((equity * tw - cur_qty * price) / price))
        if action == "SELL" and not qty:
            qty = (
                cur_qty
                if tw is None or tw <= 0 or price <= 0
                else max(0, cur_qty - math.floor(equity * tw / price))
            )
        order_type = (
            OrderType.LIMIT
            if str(d.get("order_type", "")).upper() == "LIMIT" and d.get("limit_price")
            else OrderType.MARKET
        )
        limit_price = _to_float(d.get("limit_price")) if order_type == OrderType.LIMIT else None
        conf = max(0.0, min(1.0, _to_float(d.get("confidence")) or 0.0))
        evidence = [str(e)[:300] for e in (d.get("evidence") or []) if e][:10]
        with self.db.session() as s:
            row = AgentDecision(
                run_id=run_id,
                symbol=sym,
                name=name,
                action=action,
                quantity=qty or 0,
                target_weight=tw,
                order_type=order_type.value,
                limit_price=limit_price,
                confidence=conf,
                rationale=str(d.get("rationale", ""))[:2000],
                evidence=evidence,
                created_at=self.clock.now(),
            )
            s.add(row)
            s.flush()
            dec_id = row.id
        card = {
            "decision_id": dec_id,
            "symbol": sym,
            "name": name,
            "action": action,
            "quantity": qty or 0,
            "target_weight": tw,
            "confidence": conf,
            "rationale": d.get("rationale", ""),
            "evidence": evidence,
            "price": price,
        }
        emoji = {"BUY": "🟢 매수", "SELL": "🔴 매도", "HOLD": "⚪ 유지"}[action]
        self.hub.chat(ANALYST, "decision", f"{emoji} {name or sym}", run_id=run_id, **card)
        self.hub.audit("SIGNAL", f"{action} 결정 (확신도 {conf:.0%})", sym, run_id=run_id, decision_id=dec_id)
        outcome = "유지"
        order_id = None
        if action in ("BUY", "SELL"):
            if not qty or qty <= 0 or price <= 0:
                outcome = "수량 0 → 주문 없음"
            else:
                order, risk = await self.orders.submit(
                    OrderIntent(sym, action, qty, limit_price or price, str(d.get("rationale", ""))[:200]),
                    run_id=run_id,
                    order_type=order_type,
                    limit_price=limit_price,
                )
                if order is None:
                    outcome = "리스크 차단: " + "; ".join(risk.reasons)
                else:
                    order_id = order.id
                    outcome = f"주문 {order.status} ({order.quantity}주)"
        with self.db.session() as s:
            row2 = s.get(AgentDecision, dec_id)
            assert row2 is not None
            row2.order_id, row2.outcome = order_id, outcome[:256]

    # ================================================================ 채팅 Q&A
    async def answer(self, question: str) -> None:
        self.hub.chat(USER, "text", question[:1000])
        await self._drain_replies()

    async def resume_pending(self) -> None:
        """재시작으로 답을 못 받은 오늘 질문이 있으면 이어서 답한다 (대화는 하루 단위로 이어진다)."""
        await self._drain_replies()

    async def _drain_replies(self) -> None:
        """답장은 한 번에 하나만 쓴다. 답하는 동안 들어온 질문은 끝난 뒤 한 번에 이어서 답한다 (중복 답장 방지)."""
        if self._reply_lock.locked():
            return  # 진행 중인 답장 루프가 이어서 처리한다
        async with self._reply_lock:
            while (pending := self._pending_question()) is not None:
                self._handled_question, question = pending
                await self._reply(question, pending[0])

    def _pending_question(self) -> tuple[int, str] | None:
        """오늘 마지막 질문이 아직 답을 못 받았으면 (id, 내용)."""
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.ts >= _today_start(self.clock.now()))
                    .where(ChatMessage.role.in_([USER, ANALYST]), ChatMessage.kind == "text")
                    .order_by(ChatMessage.id.desc())
                    .limit(50)
                )
            )
        # 답장마다 어느 질문까지 보고 답했는지(question_id)를 남긴다. 답하는 동안 들어온 질문은 그보다 뒤라 남는다.
        answered = self._handled_question
        for m in rows:
            if m.role == ANALYST and (m.payload or {}).get("reply_to") == "user":
                answered = max(answered, int((m.payload or {}).get("question_id") or m.id))
        newest = next((m for m in rows if m.role == USER), None)
        return (newest.id, newest.content) if newest is not None and newest.id > answered else None

    def _today_chat(self, limit: int = 30) -> list[dict[str, str]]:
        """오늘(KST) 트레이딩 룸 대화: 사용자 질문·매니저 답변·세션 요약. DB 에서 읽으므로 재시작해도 이어진다."""
        with self.db.session() as s:
            rows = list(
                s.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.ts >= _today_start(self.clock.now()))
                    .where(
                        (ChatMessage.role == USER)
                        | ((ChatMessage.role == ANALYST) & ChatMessage.kind.in_(["text", "summary"]))
                    )
                    .order_by(ChatMessage.id.desc())
                    .limit(limit)
                )
            )
        out: list[dict[str, str]] = []
        for m in reversed(rows):
            if m.role == USER:
                out.append({"role": "user", "content": m.content[:600]})
            elif m.kind == "summary":
                out.append({"role": "assistant", "content": f"[세션 #{m.run_id} 정리] {m.content[:600]}"})
            elif (m.payload or {}).get("reply_to") == "user":
                out.append({"role": "assistant", "content": m.content[:600]})
        return out

    def _log_usage(self, kind: str, run_id: int | None, usage: dict[str, int], cached: int = 0) -> None:
        model = self.llm.model if self.llm else ""
        record_usage(self.db, self.clock.now(), kind, run_id, model, usage, cached)

    async def _reply(self, question: str, question_id: int | None = None) -> None:
        p = self.persona()
        if self.llm is None:
            self.hub.chat(
                SYSTEM,
                "text",
                "💬 LLM 이 설정되지 않아 대답은 못 하지만, 메모로 저장해 뒀어요. "
                ".env 에 LLM_BASE_URL·LLM_MODEL·LLM_API_KEY 를 넣으면 대화할 수 있어요.",
            )
            return
        self._typing("reply", "답장 쓰는 중…")
        try:
            pf = await self.tb.call("get_portfolio", {})
            trades = await self.tb.call("get_trade_history", {"limit": 20})
            ctx = (
                "[포트폴리오]\n"
                + json.dumps(pf, ensure_ascii=False, default=str)[:6000]
                + "\n\n[매매 내역·실현손익]\n"
                + json.dumps(trades, ensure_ascii=False, default=str)[:5000]
                + "\n\n"
                + self._memory()
            )
            history = self._today_chat()
            if history and history[-1]["role"] == "user":
                history = history[:-1]  # 방금 질문은 아래에 따로 붙인다
            res = await self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": CHAT_SYSTEM_PROMPT.format(
                            name=p["name"], emoji=p["emoji"], account=ACCOUNT_DESC[self.live]
                        )
                        + "\n\n"
                        + ctx,
                    },
                    *history,
                    {"role": "user", "content": question[:1000]},
                ]
            )
            self._log_usage("chat", None, res.usage)
            text = strip_disclaimers(res.content)[:2000]
            self.hub.chat(
                ANALYST, "text", text or "음… 잘 모르겠어요 🤔", reply_to="user", question_id=question_id
            )
        except Exception as e:
            self.hub.chat(SYSTEM, "text", f"⚠️ 답장 중 오류: {type(e).__name__}")
        finally:
            self._typing("reply", None)


# ==================================================================== 유틸
_DISCLAIMER = re.compile(r"투자\s*권유|모의\s*투자(이|라서|일 뿐|예요|입니다)")


def strip_disclaimers(text: str) -> str:
    """'모의투자이고 투자 권유가 아니에요' 같은 면책 문장을 뺀다 (화면 하단에 이미 표시됨). 줄바꿈은 유지한다."""
    pieces = re.split(r"((?<=[.!?。])[ \t]+|(?<=요)[ \t]+|\n)", text)
    out: list[str] = []
    for i in range(0, len(pieces), 2):
        sentence, sep = pieces[i], pieces[i + 1] if i + 1 < len(pieces) else ""
        if _DISCLAIMER.search(sentence):
            if sep == "\n" and out and not out[-1].endswith("\n"):
                out.append(sep)  # 문장 중간 줄이 빠져도 줄 구분은 남긴다
            continue
        out.append(sentence + sep)
    return "".join(out).strip()


def _today_start(now: datetime) -> datetime:
    k = now.astimezone(KST)
    return k.replace(hour=0, minute=0, second=0, microsecond=0)


_SYMBOL_KEYS = ("symbol", "ticker", "code", "stock_code", "종목코드")


def normalize_decisions(raw: Any, lookup: dict[str, str]) -> list[dict[str, Any]]:
    """모델마다 다른 결정 형식을 [{symbol, action, ...}] 로 정규화한다.

    - 배열을 JSON 문자열로 감싸 보내는 경우 (Qwen 등)
    - {종목코드: {...}} 딕셔너리 형태
    - symbol 대신 ticker/code 키, 또는 종목명("삼성전자", "삼성전자(005930)")
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = (extract_json('{"d": ' + raw + "}") or {}).get("d", [])
    if isinstance(raw, dict):
        if any(k in raw for k in _SYMBOL_KEYS):
            raw = [raw]
        else:
            raw = [
                {**v, "symbol": k} if isinstance(v, dict) else {"symbol": k, "action": v}
                for k, v in raw.items()
            ]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for d in raw:
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except json.JSONDecodeError:
                continue
        if not isinstance(d, dict):
            continue
        key = (
            next((str(d[k]).strip() for k in _SYMBOL_KEYS if d.get(k)), "") or str(d.get("name", "")).strip()
        )
        sym = lookup.get(key) or lookup.get(key.upper())
        if sym is None:
            m = re.search(r"\(([A-Za-z0-9]{4,12})\)", key)  # "삼성전자(005930)"
            sym = lookup.get(m.group(1).upper()) if m else None
        if sym is None:
            sym = key
        if not sym:
            continue
        action = str(d.get("action") or d.get("decision") or "HOLD").strip().upper()
        action = {"매수": "BUY", "매도": "SELL", "유지": "HOLD", "보유": "HOLD"}.get(action, action)
        out.append({**d, "symbol": sym, "action": action})
    return out


def _acc(total: dict[str, int], u: dict[str, int]) -> None:
    for k, v in u.items():
        total[k] = total.get(k, 0) + v


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v)) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _kst(t: datetime) -> str:
    t = t if t.tzinfo else t.replace(tzinfo=UTC)
    return f"{t.astimezone(KST):%m/%d %H:%M}"
