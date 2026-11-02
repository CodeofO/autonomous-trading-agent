"""트레이딩 엔진: 데이터 동기화 → 체결 확정 → 손절/익절 감시 → 에이전트 세션 → 리포트. 킬 스위치."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select

from app.agent.runner import AgentRunner
from app.brokers.base import TERMINAL
from app.brokers.paper import PaperBroker
from app.core.clock import KST, Clock
from app.core.market_calendar import MarketCalendar
from app.db.models import EquitySnapshot, Order, StrategyConfig, WatchlistItem
from app.db.session import Database
from app.engine.events import RISK, SYSTEM, EventHub
from app.engine.orders import OrderManager, order_dict
from app.engine.settings_store import SettingsStore
from app.market.store import PriceStore
from app.risk.rules import RiskConfig, RiskContext, RiskManager
from app.strategies.rules import Strategy, build

log = logging.getLogger(__name__)

RUNNING, PAUSED, KILLED = "RUNNING", "PAUSED", "KILLED"


class TradingEngine:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        cal: MarketCalendar,
        store: PriceStore,
        broker: PaperBroker,
        hub: EventHub,
        settings: SettingsStore,
    ) -> None:
        self.db = db
        self.clock = clock
        self.cal = cal
        self.store = store
        self.broker = broker
        self.hub = hub
        self.settings = settings
        self.risk = RiskManager(self.risk_config(), cal)
        self.orders = OrderManager(db, broker, self.risk, hub, self.risk_context, name_of=store.name_of)
        self.runner: AgentRunner | None = None  # 컨테이너에서 연결

    # ------------------------------------------------------------ 설정/상태
    def risk_config(self) -> RiskConfig:
        return RiskConfig(**self.settings.get("risk"))

    def set_risk_config(self, cfg: RiskConfig) -> None:
        self.settings.set("risk", cfg.model_dump())
        self.risk.config = cfg
        self.hub.audit("CONFIG", "리스크 설정 변경", None, risk=cfg.model_dump())

    @property
    def status(self) -> str:
        return str(self.settings.get("engine").get("status", RUNNING))

    def _set_status(self, status: str, reason: str = "") -> None:
        self.settings.set("engine", {"status": status, "reason": reason, "at": self.clock.now().isoformat()})
        self.hub.state({"engine_status": status, "reason": reason})

    def enabled_strategies(self) -> list[Strategy]:
        with self.db.session() as s:
            rows = list(s.scalars(select(StrategyConfig).where(StrategyConfig.enabled.is_(True))))
        out = []
        for r in rows:
            try:
                out.append(build(r.type, r.params))
            except Exception as e:
                log.warning("전략 %s 로드 실패: %s", r.name, e)
        return out

    # ------------------------------------------------------------ 포트폴리오 뷰
    def start_of_day_equity(self, equity_now: float) -> float:
        today = self.clock.now().astimezone(KST).date().isoformat()
        sod = self.settings.get("sod")
        if sod.get("day") != today:
            sod = {"day": today, "equity": equity_now}
            self.settings.set("sod", sod)
        return float(sod["equity"])

    async def risk_context(self) -> RiskContext:
        bal = await self.broker.get_balance()
        return RiskContext(
            now=self.clock.now(),
            balance=bal,
            positions=await self.broker.get_positions(),
            open_order_symbols=self.orders.open_symbols(),
            last_order_at=self.orders.last_order_times(),
            start_of_day_equity=self.start_of_day_equity(bal.equity),
            killed=self.status == KILLED,
        )

    def order_window(self) -> str:
        now = self.clock.now()
        basis = self.cal.fill_basis(now)
        if basis == "OPEN":
            return f"지금 주문하면 {now:%Y-%m-%d} 시가로 체결 (T+1 13시에 확정)"
        if basis == "CLOSE":
            return f"지금 주문하면 {now:%Y-%m-%d} 종가로 체결 (T+1 13시에 확정)"
        s = self.cal.next_session(now)
        return f"지금은 주문 불가. 다음 접수: {s.day} 08:30 (장 시작 전 → 시가 체결)"

    async def portfolio(self) -> dict[str, Any]:
        bal = await self.broker.get_balance()
        positions = await self.broker.get_positions()
        sod = self.start_of_day_equity(bal.equity)
        today = self.clock.now().astimezone(KST).date()
        with self.db.session() as s:
            open_orders = [
                order_dict(o)
                for o in s.scalars(
                    select(Order).where(Order.status.in_(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]))
                )
            ]
            watch = [
                {"symbol": w.symbol, "name": w.name}
                for w in s.scalars(select(WatchlistItem).where(WatchlistItem.active.is_(True)))
            ]
        eq = bal.equity or 1
        return {
            "as_of": self.clock.now().isoformat(),
            "latest_price_day": (d.isoformat() if (d := self.store.latest_day()) else None),
            "order_window": self.order_window(),
            "engine_status": self.status,
            "cash": round(bal.cash),
            "available_cash": round(bal.available_cash),
            "equity": round(bal.equity),
            "initial_cash": round(bal.initial_cash),
            "total_return_pct": round((bal.equity / bal.initial_cash - 1) * 100, 2)
            if bal.initial_cash
            else 0,
            "daily_pnl_pct": round((bal.equity / sod - 1) * 100, 2) if sod else 0,
            "realized_pnl": round(bal.realized_pnl),
            "fees_paid": round(bal.fees_paid),
            "taxes_paid": round(bal.taxes_paid),
            "positions": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "quantity": p.quantity,
                    "avg_price": round(p.avg_price, 1),
                    "last_price": p.last_price,
                    "price_day": p.price_day.isoformat() if p.price_day else None,
                    "market_value": round(p.market_value),
                    "unrealized_pnl": round(p.unrealized_pnl),
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                    "weight_pct": round(p.market_value / eq * 100, 1),
                    "holding_days": (today - p.opened_day).days if p.opened_day else None,
                }
                for p in positions
            ],
            "open_orders": [
                {k: o[k] for k in ("id", "symbol", "side", "quantity", "status", "fill_basis", "trade_day")}
                for o in open_orders
            ],
            "watchlist": watch,
            "risk_limits": self.risk.config.model_dump(),
        }

    # ------------------------------------------------------------ 작업 단위
    async def sync_data(self) -> dict[str, Any]:
        """최신 공개 일봉 동기화 → 체결 확정 → 자산 스냅샷."""
        now = self.clock.now()
        latest = self.cal.latest_published_day(now)
        synced = 0
        for d in (self.cal.previous_trading_day(latest), latest):
            try:
                synced += await self.store.sync_day(d)
            except Exception as e:
                log.warning("시세 동기화 실패 %s: %s", d, e)
                self.hub.audit("ERROR", f"시세 동기화 실패 {d}: {str(e)[:200]}", None)
        # 미체결 주문의 체결일 시세도 확보
        for o in self.broker.open_orders():
            td = date.fromisoformat(o["trade_day"])
            if td <= latest and self.store.bar_on(o["symbol"], td) is None:
                try:
                    await self.store.sync_day(td)
                except Exception as e:
                    log.warning("체결일 시세 동기화 실패 %s: %s", td, e)
        settled = self.orders.settle()
        await self.snapshot_equity()
        if synced or settled:
            self.hub.state({"data_day": latest.isoformat(), "settled": settled})
        return {"latest_day": latest.isoformat(), "synced": synced, "settled": settled}

    async def check_exits(self) -> int:
        """손절/익절 감시 — 공식 종가 기준. 주문 접수 가능 시간에만."""
        if self.status != RUNNING or self.cal.fill_basis(self.clock.now()) is None:
            return 0
        n = 0
        for it in self.risk.exit_intents(await self.broker.get_positions()):
            self.hub.chat(
                RISK,
                "text",
                f"🚨 {self.store.name_of(it.symbol) or it.symbol}: {it.reason} → 전량 매도할게요.",
                symbol=it.symbol,
            )
            order, _ = await self.orders.submit(it, source="risk")
            n += order is not None
        return n

    async def run_agent(self, trigger: str) -> int | None:
        if self.runner is None:
            return None
        if self.status != RUNNING:
            self.hub.chat(SYSTEM, "text", f"⏸️ 엔진이 {self.status} 상태라 {trigger} 세션을 건너뛰었어요.")
            return None
        return await self.runner.run(trigger)

    async def morning(self) -> None:
        self.start_of_day_equity((await self.broker.get_balance()).equity)
        await self.sync_data()
        await self.check_exits()
        await self.run_agent("morning")

    async def afternoon(self) -> None:
        await self.sync_data()
        await self.check_exits()
        await self.run_agent("afternoon")

    async def snapshot_equity(self) -> None:
        bal = await self.broker.get_balance()
        with self.db.session() as s:
            s.add(EquitySnapshot(ts=self.clock.now(), equity=bal.equity, cash=bal.cash))
        self.hub.state({"equity": bal.equity, "cash": bal.cash})

    # ------------------------------------------------------------ 킬 스위치
    async def kill(self, reason: str = "사용자 요청") -> int:
        self._set_status(KILLED, reason)
        n = await self.orders.cancel_all("kill-switch")
        self.hub.audit("KILL_SWITCH", f"킬 스위치 작동: {reason}", None, canceled=n)
        self.hub.chat(
            SYSTEM, "text", f"🚨 킬 스위치 작동! 자동매매를 멈추고 미체결 주문 {n}건을 취소했어요. ({reason})"
        )
        return n

    def resume(self) -> None:
        prev = self.status
        self._set_status(RUNNING)
        self.hub.audit("ENGINE", f"엔진 재개 ({prev} → RUNNING)", None)
        self.hub.chat(SYSTEM, "text", "▶️ 자동매매를 다시 시작했어요.")

    def pause(self) -> None:
        self._set_status(PAUSED, "사용자 일시정지")
        self.hub.audit("ENGINE", "엔진 일시정지", None)
        self.hub.chat(SYSTEM, "text", "⏸️ 자동매매를 잠시 멈췄어요. 미체결 주문은 그대로 둡니다.")

    def open_order_count(self) -> int:
        return sum(1 for o in self.broker.state["orders"].values() if o["status"] not in TERMINAL)
