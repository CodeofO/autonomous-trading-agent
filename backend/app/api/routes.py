"""REST API."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from app.agent.prompts import DEFAULT_PERSONA, DEFAULT_POLICY
from app.api.auth import hash_password, issue_token, verify_password, verify_token
from app.backtest.engine import run_backtest
from app.container import Container
from app.db.models import (
    AgentDecision,
    AgentRun,
    AuditEvent,
    ChatMessage,
    DailyReport,
    EquitySnapshot,
    Fill,
    Order,
    StrategyConfig,
    User,
    WatchlistItem,
)
from app.engine.orders import order_dict
from app.market.mock_world import COMPANIES
from app.risk.rules import RiskConfig
from app.strategies.rules import REGISTRY, build, param_schema

log = logging.getLogger(__name__)
DISCLAIMER = (
    "모의투자(PAPER) 전용 교육·실험 시스템입니다. 에이전트의 판단과 예시 전략은 투자 권유가 아닙니다."
)

router = APIRouter(prefix="/api")
_bg: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def get_c(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


C = Annotated[Container, Depends(get_c)]


def require_user(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    c: Container = request.app.state.container
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "로그인이 필요합니다")
    data = verify_token(c.secret_key, authorization.split(" ", 1)[1].strip())
    if data is None:
        raise HTTPException(401, "세션이 만료됐거나 올바르지 않습니다")
    return data


U = Annotated[dict[str, Any], Depends(require_user)]


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


# ====================================================================== 인증
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


@router.get("/health")
def health(c: C) -> dict[str, str]:
    return {"status": "ok", "trading_mode": c.settings.trading_mode}


@router.get("/auth/status")
def auth_status(c: C) -> dict[str, Any]:
    with c.db.session() as s:
        n = s.scalar(select(func.count()).select_from(User)) or 0
    return {"setup_required": n == 0}


@router.post("/auth/setup")
def auth_setup(body: Credentials, c: C) -> dict[str, Any]:
    with c.db.session() as s:
        if (s.scalar(select(func.count()).select_from(User)) or 0) > 0:
            raise HTTPException(409, "이미 계정이 있습니다")
        u = User(username=body.username, password_hash=hash_password(body.password))
        s.add(u)
        s.flush()
        uid = u.id
    c.hub.audit("AUTH", "로컬 계정 생성", None, username=body.username)
    token, exp = issue_token(c.secret_key, uid, body.username, c.settings.session_ttl_hours)
    return {"token": token, "expires_at": exp, "username": body.username}


@router.post("/auth/login")
def auth_login(body: Credentials, c: C) -> dict[str, Any]:
    with c.db.session() as s:
        u = s.scalar(select(User).where(User.username == body.username))
        ok = u is not None and verify_password(body.password, u.password_hash)
        uid = u.id if u else 0
    if not ok:
        c.hub.audit("AUTH", "로그인 실패", None, username=body.username)
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
    token, exp = issue_token(c.secret_key, uid, body.username, c.settings.session_ttl_hours)
    return {"token": token, "expires_at": exp, "username": body.username}


@router.get("/auth/me")
def auth_me(user: U) -> dict[str, Any]:
    return {"username": user["u"]}


# ====================================================================== 시스템/대시보드
@router.get("/system")
def system(c: C, _: U) -> dict[str, Any]:
    s = c.settings
    llm_host = urlsplit(s.llm_base_url).hostname if s.has_llm else None
    return {
        "trading_mode": s.trading_mode,
        "data_source": c.data_source,
        "clock_mode": c.clock_mode,
        "now": c.clock.now().isoformat(),
        "sim_speed": c.scheduler.sim_speed if c.clock_mode == "simulated" else None,
        "sim_paused": c.scheduler.paused_sim,
        "llm": {
            "configured": s.has_llm,
            "model": s.llm_model if s.has_llm else None,
            "host": llm_host,
            "tool_mode": s.llm_tool_mode,
        },
        "research": c.research.sources,
        "engine_status": c.engine.status,
        "agent_running": c.runner.running,
        "order_window": c.engine.order_window(),
        "latest_price_day": (d.isoformat() if (d := c.store.latest_day()) else None),
        "next_runs": c.scheduler.next_runs(),
        "persona": c.runner.persona(),
        "disclaimer": DISCLAIMER,
    }


@router.get("/portfolio")
async def portfolio(c: C, _: U) -> dict[str, Any]:
    return await c.engine.portfolio()


@router.get("/equity")
def equity(c: C, _: U, limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        rows = list(s.scalars(select(EquitySnapshot).order_by(EquitySnapshot.id.desc()).limit(limit)))
    return [{"ts": _iso(r.ts), "equity": r.equity, "cash": r.cash} for r in reversed(rows)]


# ====================================================================== 주문
@router.get("/orders")
def orders(
    c: C, _: U, status: Literal["open", "closed", "all"] = "all", limit: int = Query(200, ge=1, le=2000)
) -> list[dict[str, Any]]:
    q = select(Order).order_by(Order.id.desc()).limit(limit)
    open_states = ["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]
    if status == "open":
        q = q.where(Order.status.in_(open_states))
    elif status == "closed":
        q = q.where(Order.status.not_in(open_states))
    with c.db.session() as s:
        return [order_dict(o) | {"name": c.store.name_of(o.symbol)} for o in s.scalars(q)]


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int, c: C, _: U) -> dict[str, Any]:
    o = await c.engine.orders.cancel(order_id)
    if o is None:
        raise HTTPException(409, "취소할 수 없는 주문입니다(이미 종료됐거나 없음)")
    return order_dict(o)


@router.get("/fills")
def fills(c: C, _: U, limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        rows = list(s.scalars(select(Fill).order_by(Fill.id.desc()).limit(limit)))
    return [
        {
            "id": f.id,
            "order_id": f.order_id,
            "symbol": f.symbol,
            "name": c.store.name_of(f.symbol),
            "side": f.side,
            "quantity": f.quantity,
            "price": f.price,
            "fee": f.fee,
            "tax": f.tax,
            "filled_at": _iso(f.filled_at),
        }
        for f in rows
    ]


# ====================================================================== 엔진 제어
class KillBody(BaseModel):
    reason: str = Field("사용자 요청", max_length=200)


@router.post("/engine/kill")
async def kill(body: KillBody, c: C, _: U) -> dict[str, Any]:
    n = await c.engine.kill(body.reason)
    return {"status": c.engine.status, "canceled": n}


@router.post("/engine/resume")
def resume(c: C, _: U) -> dict[str, Any]:
    c.engine.resume()
    return {"status": c.engine.status}


@router.post("/engine/pause")
def pause(c: C, _: U) -> dict[str, Any]:
    c.engine.pause()
    return {"status": c.engine.status}


class SimBody(BaseModel):
    action: Literal["pause", "resume", "speed"]
    speed: float | None = Field(None, gt=0, le=86400)


@router.post("/sim")
def sim_control(body: SimBody, c: C, _: U) -> dict[str, Any]:
    if c.clock_mode != "simulated":
        raise HTTPException(409, "시뮬레이션 모드가 아닙니다")
    if body.action == "pause":
        c.scheduler.paused_sim = True
    elif body.action == "resume":
        c.scheduler.paused_sim = False
    elif body.speed:
        c.scheduler.sim_speed = body.speed
    c.hub.state({"sim_paused": c.scheduler.paused_sim, "sim_speed": c.scheduler.sim_speed})
    return {"paused": c.scheduler.paused_sim, "speed": c.scheduler.sim_speed}


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
    with c.db.session() as s:
        return [_run_dict(r) for r in s.scalars(select(AgentRun).order_by(AgentRun.id.desc()).limit(limit))]


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
        "messages": [_chat_dict(m) for m in msgs],
    }


@router.get("/agent/decisions")
def agent_decisions(
    c: C, _: U, symbol: str | None = None, limit: int = Query(100, ge=1, le=1000)
) -> list[dict[str, Any]]:
    q = select(AgentDecision).order_by(AgentDecision.id.desc()).limit(limit)
    if symbol:
        q = q.where(AgentDecision.symbol == symbol)
    with c.db.session() as s:
        return [_decision_dict(d) for d in s.scalars(q)]


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
        "default_policy": DEFAULT_POLICY,
        "default_persona": DEFAULT_PERSONA,
    }


@router.put("/agent/settings")
def put_agent_settings(body: AgentSettingsBody, c: C, _: U) -> dict[str, Any]:
    c.kv.set("agent_persona", {"name": body.name, "emoji": body.emoji})
    c.kv.set("agent_policy", {"text": body.policy})
    c.hub.audit("CONFIG", "에이전트 설정 변경", None, name=body.name)
    return get_agent_settings(c, {})


# ====================================================================== 채팅
def _chat_dict(m: ChatMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "ts": _iso(m.ts),
        "role": m.role,
        "kind": m.kind,
        "content": m.content,
        "run_id": m.run_id,
        "payload": m.payload,
    }


@router.get("/chat")
def chat_history(
    c: C, _: U, before_id: int | None = None, limit: int = Query(100, ge=1, le=500)
) -> list[dict[str, Any]]:
    q = select(ChatMessage).order_by(ChatMessage.id.desc()).limit(limit)
    if before_id:
        q = q.where(ChatMessage.id < before_id)
    with c.db.session() as s:
        return [_chat_dict(m) for m in reversed(list(s.scalars(q)))]


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


@router.post("/chat", status_code=202)
async def chat_post(body: ChatBody, c: C, _: U) -> dict[str, Any]:
    _spawn(c.runner.answer(body.message.strip()))
    return {"accepted": True}


# ====================================================================== 감사 로그
@router.get("/events")
def events(
    c: C, _: U, type: str | None = None, limit: int = Query(200, ge=1, le=2000)
) -> list[dict[str, Any]]:
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if type:
        q = q.where(AuditEvent.type == type)
    with c.db.session() as s:
        return [
            {
                "id": e.id,
                "ts": _iso(e.ts),
                "type": e.type,
                "symbol": e.symbol,
                "message": e.message,
                "payload": e.payload,
            }
            for e in s.scalars(q)
        ]


# ====================================================================== 리스크/전략/관심종목
@router.get("/risk")
def get_risk(c: C, _: U) -> dict[str, Any]:
    return {"config": c.engine.risk.config.model_dump(), "schema": RiskConfig.model_json_schema()}


@router.put("/risk")
def put_risk(body: RiskConfig, c: C, _: U) -> dict[str, Any]:
    c.engine.set_risk_config(body)
    return {"config": body.model_dump()}


def _strategy_dict(r: StrategyConfig) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "type": r.type,
        "label": REGISTRY[r.type].label,
        "enabled": r.enabled,
        "params": r.params,
        "symbols": r.symbols,
        "schema": param_schema(r.type),
    }


@router.get("/strategies")
def strategies(c: C, _: U) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [_strategy_dict(r) for r in s.scalars(select(StrategyConfig).order_by(StrategyConfig.id))]


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    symbols: list[str] | None = None


@router.put("/strategies/{sid}")
def update_strategy(sid: int, body: StrategyUpdate, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        r = s.get(StrategyConfig, sid)
        if r is None:
            raise HTTPException(404, "전략 없음")
        if body.params is not None:
            try:
                build(r.type, body.params)
            except ValidationError as e:
                raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
            r.params = dict(build(r.type, body.params).params.model_dump())
        if body.enabled is not None:
            r.enabled = body.enabled
        if body.symbols is not None:
            r.symbols = [x.strip() for x in body.symbols if x.strip()]
        s.flush()
        out = _strategy_dict(r)
    c.hub.audit("CONFIG", f"전략 '{out['name']}' 변경", None, strategy=out)
    return out


@router.get("/watchlist")
def watchlist(c: C, _: U) -> list[dict[str, Any]]:
    with c.db.session() as s:
        rows = list(s.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at)))
    out = []
    for w in rows:
        b = c.store.last_bar(w.symbol)
        out.append(
            {
                "symbol": w.symbol,
                "name": w.name or (b.name if b else ""),
                "active": w.active,
                "close": b.close if b else None,
                "day": b.day.isoformat() if b else None,
            }
        )
    return out


class WatchBody(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,12}$")
    name: str = Field("", max_length=64)


@router.post("/watchlist")
def add_watch(body: WatchBody, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        if s.get(WatchlistItem, body.symbol) is None:
            s.add(WatchlistItem(symbol=body.symbol, name=body.name or c.store.name_of(body.symbol)))
    return {"ok": True}


@router.delete("/watchlist/{symbol}")
def del_watch(symbol: str, c: C, _: U) -> dict[str, Any]:
    with c.db.session() as s:
        w = s.get(WatchlistItem, symbol)
        if w:
            s.delete(w)
    return {"ok": True}


# ====================================================================== 시장/종목
@router.get("/market/universe")
def universe(c: C, _: U, q: str = "", limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    snap = c.store.snapshot()
    if not snap and c.world:
        return [{"symbol": x.symbol, "name": x.name, "market": x.market} for x in COMPANIES]
    rows = [b for b in snap if not q or q in b.symbol or q in b.name]
    rows.sort(key=lambda b: -b.market_cap)
    return [
        {"symbol": b.symbol, "name": b.name, "market": b.market, "close": b.close, "market_cap": b.market_cap}
        for b in rows[:limit]
    ]


@router.get("/market/{symbol}/candles")
async def candles(symbol: str, c: C, _: U, days: int = Query(180, ge=20, le=1000)) -> dict[str, Any]:
    latest = c.store.latest_day()
    if latest is not None:
        await c.store.ensure_history(symbol, latest, int(days * 1.5))
    bars = c.store.history(symbol, latest, days)
    with c.db.session() as s:
        fl = list(s.scalars(select(Fill).where(Fill.symbol == symbol)))
        decs = list(
            s.scalars(
                select(AgentDecision).where(AgentDecision.symbol == symbol, AgentDecision.action != "HOLD")
            )
        )
    markers = [
        {
            "time": f.filled_at.date().isoformat() if f.filled_at else None,
            "side": f.side,
            "kind": "fill",
            "text": f"{'매수' if f.side == 'BUY' else '매도'} {f.quantity}@{f.price:,.0f}",
        }
        for f in fl
    ]
    markers += [
        {
            "time": d.created_at.date().isoformat() if d.created_at else None,
            "side": d.action,
            "kind": "decision",
            "text": f"AI {d.action} ({d.confidence:.0%})",
        }
        for d in decs
    ]
    return {
        "symbol": symbol,
        "name": bars[-1].name if bars else c.store.name_of(symbol),
        "bars": [
            {
                "time": b.day.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ],
        "markers": [m for m in markers if m["time"]],
    }


@router.get("/market/{symbol}/research")
async def research(symbol: str, c: C, _: U) -> dict[str, Any]:
    tb = c.runner.tb
    name = c.store.name_of(symbol) or symbol
    fin, news, disc = await asyncio.gather(
        tb.call("get_financials", {"symbol": symbol}),
        tb.call("search_news", {"query": name, "limit": 8}),
        tb.call("get_disclosures", {"symbol": symbol, "days": 60}),
    )
    return {"symbol": symbol, "name": name, "financials": fin, "news": news, "disclosures": disc}


# ====================================================================== 백테스트 / 리포트
class BacktestBody(BaseModel):
    strategy_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(min_length=1, max_length=20)
    start: date
    end: date
    initial_cash: float = Field(10_000_000, gt=0)
    position_size: float = Field(0.1, gt=0, le=1)


@router.post("/backtest")
async def backtest(body: BacktestBody, c: C, _: U) -> dict[str, Any]:
    if body.strategy_type not in REGISTRY:
        raise HTTPException(422, "알 수 없는 전략")
    if body.end <= body.start or (body.end - body.start).days > 3 * 366:
        raise HTTPException(422, "기간은 시작<종료, 최대 3년입니다")
    try:
        build(body.strategy_type, body.params)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    latest = c.cal.latest_published_day(c.clock.now())
    end = min(body.end, latest)
    bars = []
    for sym in body.symbols:
        await c.store.ensure_history(sym, end, (end - body.start).days + 120)
        bars += [b for b in c.store.history(sym, end, 2000) if b.day >= body.start - timedelta(days=200)]
    if not bars:
        raise HTTPException(422, "해당 종목·기간의 시세가 없습니다")
    res = await run_backtest(
        bars,
        body.strategy_type,
        body.params,
        body.symbols,
        body.start,
        end,
        initial_cash=body.initial_cash,
        position_size=body.position_size,
        risk=c.engine.risk.config,
        costs=c.broker.costs,
        calendar=c.cal,
    )
    c.hub.audit(
        "BACKTEST", f"백테스트 {body.strategy_type} {body.start}~{end}: {res.total_return_pct:+.2f}%", None
    )
    return res.__dict__


@router.get("/reports")
def reports(c: C, _: U, limit: int = Query(30, ge=1, le=365)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [
            r.payload for r in s.scalars(select(DailyReport).order_by(DailyReport.day.desc()).limit(limit))
        ]


@router.post("/reports/now")
async def report_now(c: C, _: U) -> dict[str, Any]:
    from app.reports.daily import build_daily_report

    return await build_daily_report(c.engine)
