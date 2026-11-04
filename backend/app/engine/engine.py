"""트레이딩 엔진: 데이터 동기화 → 체결 확정 → 손절/익절 감시 → 에이전트 세션 → 리포트. 킬 스위치."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.agent.runner import AgentRunner
from app.brokers.base import BrokerInterface
from app.core.clock import KST, Clock
from app.core.market import MarketSpec
from app.core.market_calendar import MarketCalendar, Session
from app.db.models import EquitySnapshot, Order, StrategyConfig, WatchlistItem
from app.db.session import Database
from app.engine.events import RISK, SYSTEM, EventHub
from app.engine.orders import OrderManager, order_dict
from app.engine.settings_store import SettingsStore
from app.market.store import PriceStore
from app.market.toss import TossApiError, TossMarketProvider
from app.risk.rules import RiskConfig, RiskContext, RiskManager
from app.strategies.rules import Strategy, build

log = logging.getLogger(__name__)

RUNNING, PAUSED, KILLED = "RUNNING", "PAUSED", "KILLED"
# 장중 감시 구간: 장 시작 1분 전 ~ 마감 5분 뒤 (1분 감시·10분 자산 스냅샷·토스 연결 확인)
INTRADAY_BEFORE = timedelta(minutes=1)
INTRADAY_AFTER = timedelta(minutes=5)
SNAPSHOT_EVERY = timedelta(minutes=10)  # 장중 자산 스냅샷 간격


class TradingEngine:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        cal: MarketCalendar,
        store: PriceStore,
        broker: BrokerInterface,
        hub: EventHub,
        settings: SettingsStore,
        toss: TossMarketProvider | None = None,
        market: MarketSpec | None = None,
    ) -> None:
        # 시장 프로필 (국장 기본). 미장은 통화·시간대·캘린더 API·가격 반올림이 다르다 (D-48)
        self.market = market
        self.currency = market.currency if market else "KRW"
        self.db = db
        self.toss = toss  # 토스 실시세: 실시간 체결·장중 감시 (테스트에서만 None 가능)
        self._health: tuple[bool, float] = (True, -1e9)
        self.clock = clock
        self.cal = cal
        self.store = store
        self.broker = broker
        self.hub = hub
        self.settings = settings
        self.risk = RiskManager(self.risk_config(), cal, self.currency)
        self.orders = OrderManager(
            db, broker, self.risk, hub, self.risk_context, name_of=store.name_of, currency=self.currency
        )
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
    def today(self) -> date:
        """오늘(거래일 기준 현지 날짜). 국장 = 한국 날짜, 미장 = 뉴욕 날짜 (한국 시각 자정을 넘겨도 같은 거래일)."""
        return self.cal.local_date(self.clock.now())

    def money(self, v: float) -> float:
        """금액 반올림: 원은 정수, 달러는 센트."""
        return self.market.money(v) if self.market else round(v)

    def start_of_day_equity(self, equity_now: float) -> float:
        today = self.today().isoformat()
        sod = self.settings.get("sod")
        if sod.get("day") != today:
            sod = {"day": today, "equity": equity_now}
            self.settings.set("sod", sod)
        return float(sod["equity"])

    async def risk_context(self) -> RiskContext:
        bal, positions = await self.broker.account()  # 보유 종목 평가는 한 번만 (B1)
        return RiskContext(
            now=self.clock.now(),
            balance=bal,
            positions=positions,
            open_order_symbols=self.orders.open_symbols(),
            last_order_at=self.orders.last_order_times(),
            start_of_day_equity=self.start_of_day_equity(bal.equity),
            killed=self.status == KILLED,
        )

    def order_window(self) -> str:
        now = self.clock.now()
        basis = self.cal.fill_basis(now)
        if basis and self.broker.is_live:
            return "지금 주문하면 토스증권 실계좌로 실제 주문이 나가요 (시장가는 바로 체결, 미체결은 당일 장 마감까지 유효)"
        if self.market is not None and self.market.code == "US":
            return self._us_order_window(now, basis)
        if basis == "OPEN":
            return "지금 주문하면 09:00 장 시작 직후 현재가로 체결 (토스 실시세)"
        if basis == "CLOSE":
            return "지금 주문하면 토스 실시간 현재가로 즉시 체결 (지정가는 도달 시, 15:20 미체결분 취소)"
        s = self.cal.next_session(now)
        return f"지금은 주문 불가. 다음 접수: {s.day} 08:30 (장 시작 전 주문 → 09:00 현재가 체결)"

    def _us_order_window(self, now: datetime, basis: str | None) -> str:
        """미장 주문 안내 — 한국 시각(KST)으로 보여 준다 (정규장이 한국 시각 자정을 넘긴다)."""

        def hm(t: datetime) -> str:
            return f"{t.astimezone(KST):%H:%M}"

        s = self.cal.session(self.today())
        if basis == "OPEN" and s is not None:
            return f"지금 주문하면 미국 정규장 시작({hm(s.open)} KST) 직후 현재가로 체결 (토스 실시세, 달러)"
        if basis == "CLOSE" and s is not None:
            return (
                f"지금 주문하면 토스 실시간 현재가로 즉시 체결 (지정가는 도달 시, {hm(s.order_cutoff)} KST "
                "미체결분 취소)"
            )
        n = self.cal.next_session(now)
        pre = n.open - self.cal.pre_open_window
        return (
            f"지금은 주문 불가. 다음 접수: 미국 {n.day} 장 — {pre.astimezone(KST):%m/%d %H:%M} KST 부터 "
            f"(장 시작 {hm(n.open)} KST 현재가 체결)"
        )

    async def portfolio(self) -> dict[str, Any]:
        bal, positions = await self.broker.account()  # 보유 종목 평가는 한 번만 (B1)
        sod = self.start_of_day_equity(bal.equity)
        today = self.today()
        m = self.money
        usd = self.currency == "USD"
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
        out: dict[str, Any] = {
            "as_of": self.clock.now().isoformat(),
            "latest_price_day": (d.isoformat() if (d := self.store.latest_day()) else None),
            "order_window": self.order_window(),
            "engine_status": self.status,
            "cash": m(bal.cash),
            "available_cash": m(bal.available_cash),
            "equity": m(bal.equity),
            "initial_cash": m(bal.initial_cash),
            "total_return_pct": round((bal.equity / bal.initial_cash - 1) * 100, 2)
            if bal.initial_cash
            else 0,
            "daily_pnl_pct": round((bal.equity / sod - 1) * 100, 2) if sod else 0,
            "realized_pnl": m(bal.realized_pnl),
            "fees_paid": m(bal.fees_paid),
            "taxes_paid": m(bal.taxes_paid),
            "positions": [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "quantity": p.quantity,
                    "avg_price": round(p.avg_price, 4 if usd else 1),
                    "last_price": p.last_price,
                    "price_day": p.price_day.isoformat() if p.price_day else None,
                    "market_value": m(p.market_value),
                    "unrealized_pnl": m(p.unrealized_pnl),
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
        if self.market is not None:  # 미장/국장 구분 (프런트 금액 표기용). 국장 응답에도 붙인다
            out["market"] = self.market.code
            out["currency"] = self.market.currency
        return out

    # ------------------------------------------------------------ 작업 단위
    async def refresh_calendar(self) -> None:
        """토스 장 운영 캘린더(전일/당일/익일)를 반영 — 휴장일·단축장 대응."""
        if self.toss is None or not await self.toss_health():
            return
        try:
            api = self.market.calendar_api if self.market else "market_calendar_kr"
            self.cal.apply_toss_calendar(await getattr(self.toss.client, api)())
        except Exception as e:
            log.warning("장 운영 캘린더 조회 실패: %s", e)

    async def _sync_toss(self) -> dict[str, Any]:
        assert self.toss is not None
        today = self.today()
        symbols = {p.symbol for p in await self.broker.get_positions()}
        symbols |= self.orders.open_symbols()
        with self.db.session() as s:
            symbols |= set(s.scalars(select(WatchlistItem.symbol).where(WatchlistItem.active.is_(True))))
        n = 0
        for sym in sorted(symbols):
            try:  # 최근 일봉(당일 진행 중 봉 포함) 갱신 + 과거 이력 확보
                await self.store.ensure_history(sym, today, 200)
                n += self.store.upsert(await self.toss.fetch_history(sym, today - timedelta(days=10), today))
            except Exception as e:
                log.warning("토스 일봉 갱신 실패 %s: %s", sym, e)
        settled = await self.orders.settle()
        await self.snapshot_if_due()
        return {"latest_day": today.isoformat(), "synced": n, "settled": settled}

    def _session_window(self, now: datetime) -> Session | None:
        """지금이 장중 감시 구간(장 시작 1분 전 ~ 마감 5분 뒤)이면 그날 세션, 아니면 None. 현지 거래일 기준."""
        s = self.cal.session(self.cal.local_date(now))
        if s is None or not (s.open - INTRADAY_BEFORE <= now <= s.close + INTRADAY_AFTER):
            return None
        return s

    async def intraday(self) -> None:
        """장중 1분 주기: 지정가·장전 주문 체결, 당일 만료, 손절/익절 감시.

        장중 감시 구간 밖에서는 아무것도 하지 않는다 — 토스 연결 확인(시세 1건)도 구간 안에서만 (B6).
        """
        if self.toss is None:
            return
        now = self.clock.now()
        s = self._session_window(now)
        if s is None or not await self.toss_health():
            return
        if await self.orders.settle():
            await self.snapshot_equity()
        await self.check_exits()
        # 장중 10분마다 + 마감 뒤 첫 감시 때 한 번(그날 마감 자산)
        if now.minute % 10 == 0 or (now >= s.close and not self._has_close_snapshot(s)):
            await self.snapshot_equity()

    async def sync_data(self) -> dict[str, Any]:
        """보유·관심·미체결 종목 일봉 갱신 → 미체결 처리 → 자산 스냅샷(장중, 마감 뒤 하루 한 번)."""
        if self.toss is not None:
            return await self._sync_toss()
        settled = await self.orders.settle()
        await self.snapshot_if_due()
        return {"synced": 0, "settled": settled}

    def _has_snapshot(self, start: datetime, end: datetime) -> bool:
        """start <= ts < end 인 자산 스냅샷이 있는지."""
        with self.db.session() as db:
            got = db.scalar(
                select(EquitySnapshot.id).where(EquitySnapshot.ts >= start, EquitySnapshot.ts < end).limit(1)
            )
        return got is not None

    def _has_close_snapshot(self, s: Session) -> bool:
        """그 거래일 마감 뒤 자산 스냅샷이 이미 있는지 (다음 거래일 장 시작 전까지 범위)."""
        return self._has_snapshot(s.close, s.close + timedelta(days=1))

    async def snapshot_if_due(self) -> bool:
        """일봉 갱신·판단 세션 뒤 자산 스냅샷 기록 규칙 (B6). 기록했으면 True.

        - 장중 감시 구간: 최근 10분 안에 기록이 없을 때만 (1분 감시가 10분마다·체결 직후 기록한다)
        - 그 밖: 그 거래일 마감 뒤 첫 한 번만. 휴장일·장 시작 전에는 기록하지 않는다(자산이 바뀌지 않음)
        자산 곡선(`/api/equity`)은 장중 점들과 마감 점으로 이어진다.
        """
        now = self.clock.now()
        if self._session_window(now) is not None:
            if self._has_snapshot(now - SNAPSHOT_EVERY + timedelta(seconds=1), now + timedelta(seconds=1)):
                return False
        else:
            s = self.cal.session(self.cal.local_date(now))
            if s is None or now < s.close or self._has_close_snapshot(s):
                return False
        await self.snapshot_equity()
        return True

    @property
    def toss_ok(self) -> bool:
        """마지막 토스 연결 확인 결과 (확인하지 않는다 — 화면 표시용)."""
        return self._health[0]

    async def toss_health(self) -> bool:
        """토스 시세 접근 가능 여부. 실패 원인(허용 IP·키)을 채팅으로 1시간에 한 번 안내한다."""
        if self.toss is None:
            return True
        now = time.monotonic()
        if now - self._health[1] < 60:
            return self._health[0]
        try:
            await self.toss.client.prices([self.market.health_symbol if self.market else "005930"])
            ok, why = True, ""
        except TossApiError as e:
            ok = False
            if e.status == 403:
                why = "토스증권 허용 IP 에 이 컴퓨터의 공인 IP 가 등록되지 않았어요 (WTS > 설정 > Open API > 허용 IP 관리)"
            elif e.status == 401:
                why = "토스증권 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 을 확인해 주세요"
            else:
                why = f"토스증권 API 오류 {e.status} {e.code}"
        except Exception as e:
            ok, why = False, f"토스증권 연결 실패: {type(e).__name__}"
        self._health = (ok, now)
        if not ok:
            last = float(self.settings.get("toss_health").get("warned_at", 0))
            if time.time() - last > 3600:
                self.settings.set("toss_health", {"warned_at": time.time(), "reason": why})
                self.hub.chat(SYSTEM, "text", f"⚠️ 시세를 받을 수 없어 판단을 쉬어요: {why}")
                self.hub.audit("ERROR", why, None)
        return ok

    async def check_exits(self) -> int:
        """손절/익절 감시 — 토스 실시간 현재가 기준. 주문 접수 가능 시간에만."""
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
        if not await self.toss_health():
            return None
        if self.status != RUNNING:
            self.hub.chat(SYSTEM, "text", f"⏸️ 엔진이 {self.status} 상태라 {trigger} 세션을 건너뛰었어요.")
            return None
        return await self.runner.run(trigger)

    async def morning(self) -> None:
        await self.refresh_calendar()
        self.start_of_day_equity((await self.broker.get_balance()).equity)
        await self.sync_data()
        await self.check_exits()
        await self.run_agent("morning")

    async def hourly(self) -> None:
        """장중 정시 판단 (AGENT_INTERVAL_MINUTES 간격)."""
        await self.sync_data()
        await self.check_exits()
        await self.run_agent("hourly")

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
        return len(self.orders.open_symbols())
