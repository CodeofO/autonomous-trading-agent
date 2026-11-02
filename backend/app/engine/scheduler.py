"""스케줄러.

- real: APScheduler cron (KST) — 08:35 오전 세션, 13:20 시세 동기화·체결 확정, 14:00 오후 세션, 15:45 일일 리포트,
  10분마다 동기화 하트비트.
- simulated(Mock 전용): 실제 1초마다 시뮬레이션 시계를 `sim_speed` 초만큼 전진시키고, 같은 일정 시각을 지나면
  같은 작업을 실행한다. 작업 중에는 시계를 멈춘다(LLM 응답 대기 중 시간이 흘러가지 않도록).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.clock import KST, SimulatedClock
from app.engine.engine import TradingEngine
from app.reports.daily import build_daily_report

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[object]]


def daily_jobs(engine: TradingEngine) -> list[tuple[time, str, Job]]:
    async def report() -> None:
        await build_daily_report(engine)

    return [
        (time(8, 35), "morning", engine.morning),
        (time(13, 20), "sync", engine.sync_data),
        (time(14, 0), "afternoon", engine.afternoon),
        (time(15, 45), "report", report),
    ]


class Scheduler:
    def __init__(self, engine: TradingEngine, mode: str, sim_speed: float = 600.0) -> None:
        self.engine = engine
        self.mode = mode
        self.sim_speed = sim_speed
        self._sched: AsyncIOScheduler | None = None
        self._busy = asyncio.Lock()
        self.paused_sim = False

    def start(self) -> None:
        self._sched = AsyncIOScheduler(timezone=KST)
        if self.mode == "real":
            for t, name, job in daily_jobs(self.engine):
                self._sched.add_job(
                    self._guarded(name, job),
                    "cron",
                    day_of_week="mon-fri",
                    hour=t.hour,
                    minute=t.minute,
                    id=name,
                    misfire_grace_time=600,
                    coalesce=True,
                )
            self._sched.add_job(
                self._guarded("heartbeat", self.engine.sync_data),
                "interval",
                minutes=10,
                id="heartbeat",
                coalesce=True,
                max_instances=1,
            )
        else:
            self._sched.add_job(
                self.sim_tick, "interval", seconds=1, id="sim", coalesce=True, max_instances=1
            )
        self._sched.start()
        log.info("스케줄러 시작 (%s)", self.mode)

    def shutdown(self) -> None:
        if self._sched:
            self._sched.shutdown(wait=False)

    def _guarded(self, name: str, job: Job) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            if self._busy.locked():
                log.info("작업 %s 건너뜀(다른 작업 실행 중)", name)
                return
            async with self._busy:
                try:
                    await job()
                except Exception:
                    log.exception("스케줄 작업 실패: %s", name)
                    self.engine.hub.audit("ERROR", f"스케줄 작업 실패: {name}", None)

        return run

    # ------------------------------------------------------------ 시뮬레이션
    async def sim_tick(self) -> None:
        if self.paused_sim or self._busy.locked():
            return
        if self.engine.runner is not None and self.engine.runner.running:
            return  # 판단 세션(수동 포함) 중에는 시뮬레이션 시계를 멈춘다
        clock = self.engine.clock
        assert isinstance(clock, SimulatedClock)
        before = clock.now()
        after = before + timedelta(seconds=self.sim_speed)
        clock.set(after)
        self.engine.settings.set("sim_clock", {"now": after.isoformat()})  # 재시작 시 이어서 진행
        for name, job in self.due_jobs(before, after):
            await self._guarded(name, job)()
        self.engine.hub.state({"sim_time": clock.now().isoformat()})

    def due_jobs(self, before: datetime, after: datetime) -> list[tuple[str, Job]]:
        """(before, after] 사이에 지나간 일정 작업 (거래일만)."""
        out: list[tuple[str, Job]] = []
        d = before.astimezone(KST).date()
        while d <= after.astimezone(KST).date():
            if self.engine.cal.is_trading_day(d):
                for t, name, job in daily_jobs(self.engine):
                    at = datetime.combine(d, t, KST)
                    if before < at <= after:
                        out.append((name, job))
            d += timedelta(days=1)
        return out

    def next_runs(self) -> list[dict[str, str]]:
        now = self.engine.clock.now()
        out = []
        d = now.astimezone(KST).date()
        for _ in range(10):
            if self.engine.cal.is_trading_day(d):
                for t, name, _job in daily_jobs(self.engine):
                    at = datetime.combine(d, t, KST)
                    if at > now:
                        out.append({"name": name, "at": at.isoformat()})
            if len(out) >= 4:
                break
            d += timedelta(days=1)
        return out[:4]
