"""전략 실험실 (D-62): 여러 투자 전략을 시장마다 동시에 모의로 돌려 비교한다.

- 주 계좌: 시장 스택 그대로(국장 TRADING_MODE, 미장 US_TRADING_MODE). 따르는 전략은 kv `strategy` 로 고른다.
  실거래는 이 계좌에서만 — 사용자가 고른 전략 하나로.
- 시나리오: 켠 전략마다 **자기 가상 계좌**(PaperBroker, 같은 시작 금액·비용 규칙)·기록 DB·매니저를 둔다.
  시세·캘린더·장외 세션 설정·LLM 은 주 계좌 스택과 같이 쓴다. 휴대폰 알림·구글 검색(유료)은 쓰지 않는다.
  PaperBroker 는 모의(paper)가 아니면 주문을 거부하므로 시나리오는 코드로 실거래가 막혀 있다.
- 실행: 주 계좌 판단(장전·정시)이 끝나면 켠 시나리오를 차례로 같은 종류로 판단한다(LLM 동시 호출을 늘리지 않게).
  지난 차례가 아직 돌고 있으면 이번 차례는 건너뛴다. 장중 1분 감시(지정가·장외 체결, 손절/익절)도 같이 돈다.
- 저장: 주 계좌 kv `scenarios` = {"enabled": [전략 id, …]}. 시나리오 DB 는 data/scenarios/{시장}_{전략}.db
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.agent.runner import STRATEGY_KEY
from app.db.models import AgentRun, EquitySnapshot, Fill, Order
from app.engine.events import SYSTEM
from app.engine.maintenance import run_maintenance
from app.risk.rules import RiskConfig
from app.strategies import styles

if TYPE_CHECKING:
    from app.container import Container

log = logging.getLogger(__name__)

KEY = "scenarios"
RISK_KEY = "risk_by_strategy"  # 주 계좌 kv: {전략 id: 그 전략으로 운용할 때의 리스크 설정} (D-64)
MAX_SCENARIOS = 6  # 한 시장에서 동시에 켤 수 있는 시나리오 수 (판단 한 차례가 너무 길어지지 않게)

Builder = Callable[["Container", str], "Container"]


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).isoformat()


def risk_text(r: RiskConfig) -> str:
    """리스크 한도 한 줄: 손절 −20% · 익절 +100% · 최소 보유 30일 · 종목당 15% · 최대 12종목."""
    hold = f" · 최소 보유 {r.min_hold_days}일" if r.min_hold_days else ""
    return (
        f"손절 {r.stop_loss:.0%} · 익절 +{r.take_profit:.0%}{hold} · "
        f"종목당 {r.max_position_weight:.0%} · 최대 {r.max_positions}종목"
    )


def risk_summary(r: RiskConfig) -> dict[str, Any]:
    return {
        "stop_loss": r.stop_loss,
        "take_profit": r.take_profit,
        "min_hold_days": r.min_hold_days,
        "max_position_weight": r.max_position_weight,
        "max_positions": r.max_positions,
        "text": risk_text(r),
    }


class LabError(ValueError):
    """화면에 그대로 보여 줄 한국어 사유 (API 400)."""


class ScenarioLab:
    def __init__(self, main: Container, build: Builder) -> None:
        self.main = main
        self._build = build
        self.stacks: dict[str, Container] = {}
        self._round = asyncio.Lock()  # 판단 한 차례(켠 시나리오 전부)는 한 번에 하나
        self.running: str | None = None  # 지금 판단 중인 시나리오
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ 설정
    @property
    def us(self) -> bool:
        return self.main.code == "US"

    def main_id(self) -> str:
        sid = str(self.main.kv.get(STRATEGY_KEY).get("id") or styles.DEFAULT_ID)
        return sid if styles.get(sid) else styles.DEFAULT_ID

    def enabled(self) -> list[str]:
        """켠 시나리오 (카탈로그 순서). 주 계좌 전략과 같은 것도 따로 돌 수 있다(모의 대조군)."""
        on = set(self.main.kv.get(KEY).get("enabled") or [])
        return [sid for sid in styles.STYLES if sid in on]

    def set_main(self, sid: str) -> str:
        style = styles.get(sid)
        if style is None:
            raise LabError("없는 전략이에요")
        prev = self.main_id()
        if prev == sid:
            return sid
        self.main.kv.set(STRATEGY_KEY, {"id": sid})
        # 리스크 한도도 전략마다 (D-64): 지금 한도는 이전 전략 몫으로 기억하고, 새 전략 몫(없으면 기본값)을 꺼낸다
        engine = self.main.engine
        saved = dict(self.main.kv.get(RISK_KEY))
        saved[prev] = engine.risk.config.model_dump()
        risk = (
            RiskConfig(**saved[sid])
            if isinstance(saved.get(sid), dict)
            else style.risk_config(engine.risk.config)
        )
        self.main.kv.set(RISK_KEY, saved)
        engine.set_risk_config(risk)
        live = bool(self.main.broker.is_live)
        account = "🔴 실거래 계좌" if live else "모의 계좌"
        msg = f"주 계좌 전략 변경: {styles.STYLES[prev].name} → {style.name}"
        self.main.hub.audit("CONFIG", msg, None, strategy=sid, previous=prev, live=live)
        self.main.hub.chat(
            SYSTEM,
            "text",
            f"{style.emoji} {account}을 이제 '{style.name}' 전략으로 운용해요 — {style.summary}. "
            f"리스크 한도도 이 전략 것으로 바꿨어요: {risk_text(risk)}. 다음 판단부터 적용돼요.",
        )
        return sid

    def apply_preset(self) -> RiskConfig:
        """주 계좌 리스크 한도를 지금 전략의 기본값으로 (1회 주문 금액은 그대로)."""
        style = styles.STYLES[self.main_id()]
        engine = self.main.engine
        risk = style.risk_config(engine.risk.config)
        engine.set_risk_config(risk)
        self.main.hub.chat(
            SYSTEM, "text", f"🛡️ 리스크 한도를 '{style.name}' 전략 기본값으로 바꿨어요: {risk_text(risk)}."
        )
        return risk

    def set_enabled(self, ids: list[str]) -> list[str]:
        unknown = [i for i in ids if styles.get(i) is None]
        if unknown:
            raise LabError(f"없는 전략이에요: {', '.join(unknown)}")
        want = [sid for sid in styles.STYLES if sid in set(ids)]
        if len(want) > MAX_SCENARIOS:
            raise LabError(f"시나리오는 한 시장에 {MAX_SCENARIOS}개까지 켤 수 있어요")
        before = self.enabled()
        self.main.kv.set(KEY, {"enabled": want})
        self.ensure()
        for sid in set(self.stacks) - set(want):
            self.stacks.pop(sid).db.engine.dispose()  # 기록 DB 는 남는다 — 다시 켜면 이어서
        added = [styles.STYLES[s].name for s in want if s not in before]
        removed = [styles.STYLES[s].name for s in before if s not in want]
        if added or removed:
            parts = ([f"켬 {', '.join(added)}"] if added else []) + (
                [f"끔 {', '.join(removed)}"] if removed else []
            )
            self.main.hub.audit("CONFIG", f"전략 시나리오 {' / '.join(parts)}", None, scenarios=want)
        return want

    def ensure(self) -> None:
        """켠 시나리오의 스택을 만든다 (이미 있으면 그대로). 만들다 실패한 것은 건너뛰고 로그만."""
        for sid in self.enabled():
            if sid in self.stacks:
                continue
            try:
                self.stacks[sid] = self._build(self.main, sid)
            except Exception:
                log.exception("전략 시나리오 %s 준비 실패", sid)

    def stack(self, sid: str) -> Container:
        if styles.get(sid) is None:
            raise LabError("없는 전략이에요")
        if sid not in self.enabled():
            raise LabError("켜지 않은 시나리오예요")
        self.ensure()
        st = self.stacks.get(sid)
        if st is None:
            raise LabError("시나리오를 준비하지 못했어요 — 서버 로그를 확인해 주세요")
        return st

    # ------------------------------------------------------------ 실행
    @property
    def round_running(self) -> bool:
        return self._round.locked()

    async def run_round(self, trigger: str) -> list[str]:
        """주 계좌 판단 뒤: 켠 시나리오를 차례로 같은 종류(장전 morning / 정시 hourly)로 판단. 돈 시나리오 id."""
        if not self.enabled():
            return []
        if self._round.locked():
            log.info("전략 시나리오 판단 건너뜀 — 지난 차례가 아직 진행 중")
            return []
        done: list[str] = []
        async with self._round:
            self.ensure()
            for sid in self.enabled():
                st = self.stacks.get(sid)
                if st is None:
                    continue
                st.runner.llm = self.main.runner.llm  # LLM 선택(무료/유료·모델)은 주 계좌를 따른다
                self.running = sid
                try:
                    if trigger == "morning":
                        await st.engine.morning()
                    elif trigger == "manual":
                        await st.engine.sync_data()
                        await st.engine.run_agent("manual")
                    else:
                        await st.engine.hourly()
                    done.append(sid)
                except Exception:
                    log.exception("전략 시나리오 %s 판단 실패", sid)
                finally:
                    self.running = None
        return done

    def start_one(self, sid: str) -> None:
        """시나리오 하나를 지금 판단 (백그라운드). 다른 차례가 돌고 있으면 거절."""
        st = self.stack(sid)
        if self._round.locked() or st.runner.running:
            raise LabError("다른 시나리오가 판단 중이에요 — 끝나면 다시 눌러 주세요")

        async def run() -> None:
            async with self._round:
                st.runner.llm = self.main.runner.llm
                self.running = sid
                try:
                    await st.engine.sync_data()
                    await st.engine.run_agent("manual")
                except Exception:
                    log.exception("전략 시나리오 %s 수동 판단 실패", sid)
                finally:
                    self.running = None

        t = asyncio.ensure_future(run())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def intraday(self) -> None:
        """장중 1분 감시: 지정가·장외 체결, 당일 만료, 손절/익절 (시나리오마다)."""
        for sid, st in list(self.stacks.items()):
            try:
                await st.engine.intraday()
            except Exception:
                log.exception("전략 시나리오 %s 장중 감시 실패", sid)

    def maintenance(self) -> None:
        for st in self.stacks.values():
            try:
                run_maintenance(st.db, st.clock.now(), st.broker)
            except Exception:
                log.exception("전략 시나리오 정리 실패")

    def reset(self, sid: str) -> None:
        """시나리오 계좌를 처음 금액으로 다시 시작 (기록은 남기고, 비교·기억은 지금부터)."""
        st = self.stack(sid)
        if self.running == sid or st.runner.running:
            raise LabError("판단 중에는 다시 시작할 수 없어요")
        st.broker.reset()  # type: ignore[union-attr]
        now = st.clock.now()
        st.kv.set("account_source", {**st.kv.get("account_source"), "since": now.isoformat()})
        st.kv.set("sod", {})
        st.hub.audit("ACCOUNT_RESET", "전략 시나리오 계좌 다시 시작", None)
        self.main.hub.audit("CONFIG", f"전략 시나리오 다시 시작: {styles.STYLES[sid].name}", None)

    # ------------------------------------------------------------ 조회
    @staticmethod
    def _since(st: Container) -> datetime | None:
        raw = st.kv.get("account_source").get("since")
        return datetime.fromisoformat(raw) if raw else None

    async def _row(self, st: Container, *, sid: str, kind: str) -> dict[str, Any]:
        style = styles.STYLES[sid]
        bal, positions = await _account(st)
        since = self._since(st)
        initial = float(bal.initial_cash or 0)
        with st.db.session() as s:
            q = select(func.count(Fill.id))
            if since is not None:
                q = q.where(Fill.filled_at >= since)
            fills = int(s.scalar(q) or 0)
            rq = select(AgentRun).where(AgentRun.status.in_(["DONE", "FAILED"]))
            if since is not None:
                rq = rq.where(AgentRun.started_at >= since)
            last = s.scalars(rq.order_by(AgentRun.id.desc()).limit(1)).first()
            runs = int(
                s.scalar(
                    select(func.count(AgentRun.id)).where(
                        *([AgentRun.started_at >= since] if since is not None else [])
                    )
                )
                or 0
            )
        equity = float(bal.equity)
        total = sum(p.market_value for p in positions) or 0.0
        return {
            "id": sid,
            "kind": kind,  # main | scenario
            "name": style.name,
            "emoji": style.emoji,
            "persona": style.persona,
            "summary": style.summary,
            "live": bool(st.broker.is_live),
            "equity": equity,
            "cash": float(bal.cash),
            "initial_cash": initial or None,
            "return_pct": round((equity / initial - 1) * 100, 2) if initial > 0 else None,
            "positions": len(positions),
            "top_positions": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "weight": round(p.market_value / equity * 100, 1) if equity else 0.0,
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                }
                for p in sorted(positions, key=lambda x: -x.market_value)[:3]
            ],
            "invested_pct": round(total / equity * 100, 1) if equity else 0.0,
            "fills": fills,
            "runs": runs,
            "since": _iso(since),
            "last_run": (
                {
                    "id": last.id,
                    "at": _iso(last.finished_at or last.started_at),
                    "status": last.status,
                    "summary": (last.summary or last.error or "")[:300],
                }
                if last is not None
                else None
            ),
            "running": bool(st.runner.running),
            "risk": risk_summary(st.engine.risk.config),
        }

    async def view(self) -> dict[str, Any]:
        self.ensure()
        rows = [await self._safe_row(self.main, sid=self.main_id(), kind="main")]
        for sid in self.enabled():
            st = self.stacks.get(sid)
            if st is not None:
                rows.append(await self._safe_row(st, sid=sid, kind="scenario"))
        llm = self.main.runner.llm
        return {
            "market": self.main.code,
            "currency": self.main.market.currency if self.main.market else "KRW",
            "live": bool(self.main.broker.is_live),
            "main": self.main_id(),
            "enabled": self.enabled(),
            "max_scenarios": MAX_SCENARIOS,
            "styles": styles.catalog(self.us),
            "rows": rows,
            "round_running": self.round_running,
            "running": self.running,
            "llm": llm.model.split("@", 1)[0]
            if llm
            else None,  # 모델 이름만 (내부 주소는 화면에 내지 않는다)
            "next_run": next(iter(self.main.scheduler.next_runs(1)), None),
        }

    async def _safe_row(self, st: Container, *, sid: str, kind: str) -> dict[str, Any]:
        try:
            return await self._row(st, sid=sid, kind=kind)
        except Exception as e:  # 실거래 잔고 조회 실패 등 — 표의 한 줄만 비운다
            log.warning("전략 실험실 %s 줄 계산 실패: %s", sid, type(e).__name__)
            style = styles.STYLES[sid]
            return {
                "id": sid,
                "kind": kind,
                "name": style.name,
                "emoji": style.emoji,
                "persona": style.persona,
                "summary": style.summary,
                "live": bool(st.broker.is_live),
                "error": f"계좌를 읽지 못했어요 ({type(e).__name__})",
                "running": bool(st.runner.running),
            }

    async def detail(self, sid: str) -> dict[str, Any]:
        st = self.stack(sid)
        since = self._since(st)
        _bal, positions = await _account(st)
        with st.db.session() as s:
            oq = select(Order)
            rq = select(AgentRun)
            eq = select(EquitySnapshot)
            if since is not None:
                oq = oq.where(Order.created_at >= since)
                rq = rq.where(AgentRun.started_at >= since)
                eq = eq.where(EquitySnapshot.ts >= since)
            orders = list(s.scalars(oq.order_by(Order.id.desc()).limit(30)))
            runs = list(s.scalars(rq.order_by(AgentRun.id.desc()).limit(10)))
            curve = list(s.scalars(eq.order_by(EquitySnapshot.ts.desc()).limit(500)))
            order_rows = [
                {
                    "id": o.id,
                    "symbol": o.symbol,
                    "name": st.store.name_of(o.symbol),
                    "side": o.side,
                    "quantity": o.quantity,
                    "status": o.status,
                    "filled_qty": o.filled_qty,
                    "avg_fill_price": o.avg_fill_price,
                    "created_at": _iso(o.created_at),
                    "reason": o.reject_reason or o.reason,
                }
                for o in orders
            ]
            run_rows = [
                {
                    "id": r.id,
                    "trigger": r.trigger,
                    "status": r.status,
                    "started_at": _iso(r.started_at),
                    "summary": (r.summary or r.error or "")[:600],
                    "market_view": (r.market_view or "")[:400],
                }
                for r in runs
            ]
            points = [{"ts": _iso(p.ts), "equity": p.equity} for p in reversed(curve)]
        return {
            "id": sid,
            "positions": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                    "last_price": p.last_price,
                    "market_value": p.market_value,
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                }
                for p in positions
            ],
            "orders": order_rows,
            "runs": run_rows,
            "equity": points,
        }


async def _account(st: Container) -> tuple[Any, list[Any]]:
    account = getattr(st.broker, "account", None)
    if account is not None:
        bal, positions = await account()
        return bal, list(positions)
    positions = await st.broker.get_positions()
    return await st.broker.get_balance(), list(positions)
