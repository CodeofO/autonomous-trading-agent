"""에이전트 API: 판단 세션·결정·진행 상황·구성·설정."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent import topics
from app.agent.activity import TOOL_GROUPS
from app.agent.prompts import DEFAULT_PERSONA, DEFAULT_POLICY, US_DEFAULT_POLICY
from app.agent.tools import openai_tools
from app.api.routes.common import C, U, _is_live, _is_us, _iso, _since, _spawn, chat_dict
from app.container import Container, market_profile
from app.db.models import AgentDecision, AgentRun, ChatMessage

router = APIRouter()


# ====================================================================== 에이전트
@router.post("/agent/run", status_code=202)
async def agent_run(c: C, _: U) -> dict[str, Any]:
    if c.runner.running:
        raise HTTPException(409, "이미 세션이 진행 중입니다")
    if c.engine.status != "RUNNING":
        raise HTTPException(409, f"엔진이 {c.engine.status} 상태입니다")

    async def job() -> None:
        await c.engine.sync_data()
        await c.engine.run_agent("manual")

    _spawn(job())
    return {"accepted": True}


def _run_dict(r: AgentRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "trigger": r.trigger,
        "mode": r.mode,
        "model": r.model,
        "status": r.status,
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
        "summary": r.summary,
        "market_view": r.market_view,
        "error": r.error,
        "usage": r.usage,
    }


def _decision_dict(d: AgentDecision) -> dict[str, Any]:
    return {
        "id": d.id,
        "run_id": d.run_id,
        "symbol": d.symbol,
        "name": d.name,
        "action": d.action,
        "quantity": d.quantity,
        "target_weight": d.target_weight,
        "confidence": d.confidence,
        "rationale": d.rationale,
        "evidence": d.evidence,
        "order_id": d.order_id,
        "outcome": d.outcome,
        "created_at": _iso(d.created_at),
    }


@router.get("/agent/runs")
def agent_runs(c: C, _: U, limit: int = Query(30, ge=1, le=500)) -> list[dict[str, Any]]:
    q = select(AgentRun).order_by(AgentRun.id.desc()).limit(limit)
    if since := _since(c):
        q = q.where(AgentRun.started_at >= since)
    with c.db.session() as s:
        return [_run_dict(r) for r in s.scalars(q)]


@router.get("/agent/runs/{run_id}")
def agent_run_detail(run_id: int, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        r = s.get(AgentRun, run_id)
        if r is None:
            raise HTTPException(404, "세션 없음")
        decs = list(s.scalars(select(AgentDecision).where(AgentDecision.run_id == run_id)))
        msgs = list(
            s.scalars(select(ChatMessage).where(ChatMessage.run_id == run_id).order_by(ChatMessage.id))
        )
    return {
        "run": _run_dict(r),
        "decisions": [_decision_dict(d) for d in decs],
        "messages": [chat_dict(m) for m in msgs],
    }


@router.get("/agent/decisions")
def agent_decisions(
    c: C, _: U, symbol: str | None = None, limit: int = Query(100, ge=1, le=1000)
) -> list[dict[str, Any]]:
    q = select(AgentDecision).order_by(AgentDecision.id.desc()).limit(limit)
    if symbol:
        q = q.where(AgentDecision.symbol == symbol)
    if since := _since(c):
        q = q.where(AgentDecision.created_at >= since)
    with c.db.session() as s:
        return [_decision_dict(d) for d in s.scalars(q)]


@router.get("/agent/progress")
def agent_progress(c: C, _: U) -> dict[str, Any]:
    """진행 중인 판단 세션의 현재 단계 (트레이딩 룸 활동 패널을 새로고침 직후에도 채우기 위함)."""
    return c.runner.progress


@router.get("/agent/structure")
def agent_structure(c: C, _: U) -> dict[str, Any]:
    """에이전트 구성(도구·데이터 출처·스케줄·리스크 규칙)을 실제 설정값 그대로 돌려준다."""
    s = c.settings
    us, live = _is_us(c), _is_live(c)
    account = "토스 실계좌" if live else "모의계좌"
    ready = {
        "토스증권": c.engine.toss_ok,
        "DART": c.research.sources["financials"] == "DART",
        "NAVER": c.research.sources["news"] == "NAVER" and not c.research.news_error,
        "GOOGLE": c.research.sources["web"] == "GOOGLE",
        account: True,
    }
    if us:
        ready.pop("DART")  # 국내 전용 (미장 도구에 없음)
    notes = {"NAVER": "키 인증 실패"} if c.research.news_error else {}
    if not ready["GOOGLE"]:
        notes["GOOGLE"] = "GEMINI_API_KEY 없음 (유료)"
    tools = []
    # 구글 검색은 키가 없으면 매니저에게 주지 않지만, 화면에는 '연결 안 됨'으로 보여 준다 (D-49)
    web_limit = c.runner.tb.web_limit or s.google_search_max_per_session
    for t in openai_tools(toss=c.toss is not None, market=c.code, web_limit=web_limit):
        f = t["function"]
        group, source = TOOL_GROUPS.get(f["name"], ("market", "토스증권"))
        source = account if source == "모의계좌" else source
        tools.append(
            {
                "name": f["name"],
                "description": f["description"],
                "group": group,
                "source": source,
                "ready": ready.get(source, False),
            }
        )
    mode = s.llm_tool_mode if c.runner.llm else "rules"
    return {
        "persona": c.runner.persona(),
        "llm": {
            "configured": c.runner.llm is not None,
            "model": c.runner.llm.model if c.runner.llm else None,
            "profile": market_profile(c.llm_kv or c.kv, s, c.code),
            "mode": mode,
            "max_steps": s.llm_max_steps,
            "max_tokens": s.llm_max_tokens,
        },
        "sources": [{"name": k, "ready": v, "note": notes.get(k)} for k, v in ready.items()],
        "memory": {
            # 매니저가 무엇을 '기억'하는지 (D-47): 별도 장기 기억 저장소는 없고, 매번 DB 에서 아래를 다시 읽어 넣는다
            "rules": [
                {
                    "name": "메모 카드",
                    "where": "판단 세션·답장",
                    "detail": "메모 화면에서 만든 카드 중 켜져 있고 만료되지 않은 것 — 관심 종목·주목할 종목·"
                    "피할 종목(매수하지 않음)·운용 지시. 리스크 한도가 항상 우선",
                },
                {
                    "name": "최근 판단 세션",
                    "where": "판단 세션·답장",
                    "detail": f"완료된 세션 최근 {c.runner.MEMORY_RUNS}개의 요약(160자)과 사고판 종목",
                },
                {
                    "name": "대화에서 되풀이한 관심사",
                    "where": "판단 세션·답장",
                    "detail": "트레이딩 룸에서 꺼낸 주제(종목·업종·테마·지시)를 주제별로 모은다. 여러 날 되풀이해 물으면 "
                    "중요도가 오르고 오래 남고, 한 번 묻고 만 주제는 그날만 들어가고 며칠 안에 빠진다. 새 메시지가 있으면 "
                    "판단 세션 전에 매니저가 주제마다 올림·유지·내림·잊기를 정한다(LLM 1번). 중요도 "
                    f"{topics.THRESHOLD} 이상·최대 {topics.MAX_IN_PROMPT}개, 메모 화면에서 늘 기억·빼기 "
                    + ("(지금 켜짐)" if topics.enabled(c.kv) else "(지금 꺼짐)"),
                },
                {
                    "name": "오늘 대화",
                    "where": "답장",
                    "detail": "오늘(한국 시각) 트레이딩 룸의 내 질문·매니저 답·세션 정리 최근 30개. 자정이 지나면 빠진다",
                },
                {
                    "name": "계좌·매매 내역",
                    "where": "판단 세션·답장",
                    "detail": "기억이 아니라 매번 새로 조회 (포트폴리오·최근 매매 20건)",
                },
            ],
            **c.runner.memory_snapshot(),
        },
        "tools": tools,
        "schedule": _schedule(c),
        "risk": c.engine.risk_config().model_dump(),
        "paper": {
            "initial_cash": c.market.initial_cash if c.market else s.paper_initial_cash,
            "commission_rate": c.market.commission_rate if c.market else s.paper_commission_rate,
            "sell_tax_rate": c.market.sell_tax_rate if c.market else s.paper_sell_tax_rate,
            "slippage_bps": c.market.slippage_bps if c.market else s.paper_slippage_bps,
            "currency": c.market.currency if c.market else "KRW",
        },
        "market": c.code,
        "trading_mode": "live" if live else "paper",
        "live_daily_buy_limit": (
            (s.us_live_daily_buy_limit if us else s.live_daily_buy_limit) if live else None
        ),
        "engine_status": c.engine.status,
        "agent_running": c.runner.running,
    }


def _schedule(c: Container) -> dict[str, Any]:
    """오늘(또는 다음) 거래일의 판단 시각 — 한국 시각. 판단 일정 설정(D-51)을 그대로 반영한다."""
    base = {"interval_minutes": c.scheduler.interval_minutes, "monitor_minutes": 1, "refresh_minutes": 10}
    return {**base, **c.scheduler.schedule_info()}


class AgentSettingsBody(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    emoji: str = Field(min_length=1, max_length=8)
    policy: str = Field(min_length=10, max_length=4000)


@router.get("/agent/settings")
def get_agent_settings(c: C, _: U) -> dict[str, Any]:
    p = c.runner.persona()
    return {
        "name": p["name"],
        "emoji": p["emoji"],
        "policy": c.runner.policy(),
        "default_policy": US_DEFAULT_POLICY if _is_us(c) else DEFAULT_POLICY,
        "default_persona": DEFAULT_PERSONA,
    }


@router.put("/agent/settings")
def put_agent_settings(body: AgentSettingsBody, c: C, _: U) -> dict[str, Any]:
    c.kv.set("agent_persona", {"name": body.name, "emoji": body.emoji})
    c.kv.set("agent_policy", {"text": body.policy})
    c.hub.audit("CONFIG", "에이전트 설정 변경", None, name=body.name)
    return get_agent_settings(c, {})
