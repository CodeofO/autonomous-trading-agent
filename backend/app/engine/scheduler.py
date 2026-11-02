"""스케줄러 (실제 한국 시각, APScheduler cron).

거래일(토스 장 운영 캘린더 기준) 일정:
- 08:35 장전 판단 (장 시작 직후 현재가로 체결)
- 장중 판단: 09:00 + N×AGENT_INTERVAL_MINUTES (기본 60분 → 10:00 ~ 15:00 매시 정각), 15:20 주문 마감 전까지
- 매 1분: 지정가·장전 주문 체결, 15:20 당일 만료, 손절/익절 감시
- 10분마다: 보유·관심 종목 일봉 갱신
- 15:45 일일 리포트
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.clock import KST
from app.core.market_calendar import CONTINUOUS_CLOSE, REGULAR_OPEN
from app.engine.engine import TradingEngine
from app.reports.daily import build_daily_report

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[object]]


def session_times(interval_minutes: int) -> list[time]:
    """장중 판단 시각. interval 0 이하이면 14:00 한 번(하루 2회 모드)."""
    if interval_minutes <= 0:
        return [time(14, 0)]
    base = date(2000, 1, 3)
    t = datetime.combine(base, REGULAR_OPEN) + timedelta(minutes=interval_minutes)
    cutoff = datetime.combine(base, CONTINUOUS_CLOSE)
    out: list[time] = []
    while t < cutoff:
        out.append(t.time())
        t += timedelta(minutes=interval_minutes)
    return out


def daily_jobs(engine: TradingEngine, interval_minutes: int) -> list[tuple[time, str, Job]]:
    async def report() -> None:
        await build_daily_report(engine)

    jobs: list[tuple[time, str, Job]] = [(time(8, 35), "morning", engine.morning)]
    jobs += [(t, "hourly", engine.hourly) for t in session_times(interval_minutes)]
    jobs.append((time(15, 45), "report", report))
    return jobs


class Scheduler:
    def __init__(self, engine: TradingEngine, interval_minutes: int = 60) -> None:
        self.engine = engine
        self.interval_minutes = interval_minutes
        self._sched: AsyncIOScheduler | None = None
        self._busy = asyncio.Lock()

    def start(self) -> None:
        self._sched = AsyncIOScheduler(timezone=KST)
        for t, name, job in daily_jobs(self.engine, self.interval_minutes):
            self._sched.add_job(
                self._guarded(name, job, trading_day_only=True),
                "cron",
                hour=t.hour,
                minute=t.minute,
                id=f"{name}-{t:%H%M}",
                misfire_grace_time=600,
                coalesce=True,
            )
        self._sched.add_job(
            self._guarded("intraday", self.engine.intraday, busy_skip=False),
            "interval",
            minutes=1,
            id="intraday",
            coalesce=True,
            max_instances=1,
        )
        self._sched.add_job(
            self._guarded("refresh", self.engine.sync_data),
            "interval",
            minutes=10,
            id="refresh",
            coalesce=True,
            max_instances=1,
        )
        self._sched.start()
        times = ", ".join(f"{t:%H:%M}" for t in session_times(self.interval_minutes))
        log.info("스케줄러 시작 — 장전 08:35, 장중 판단 %s", times)

    def shutdown(self) -> None:
        if self._sched:
            self._sched.shutdown(wait=False)

    def _guarded(
        self, name: str, job: Job, *, trading_day_only: bool = False, busy_skip: bool = True
    ) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            today = self.engine.clock.now().astimezone(KST).date()
            if trading_day_only and not self.engine.cal.is_trading_day(today):
                return
            if busy_skip and self._busy.locked():
                log.info("작업 %s 건너뜀(다른 작업 실행 중)", name)
                return
            try:
                if busy_skip:
                    async with self._busy:
                        await job()
                else:
                    await job()
            except Exception:
                log.exception("스케줄 작업 실패: %s", name)
                self.engine.hub.audit("ERROR", f"스케줄 작업 실패: {name}", None)

        return run

    def next_runs(self, n: int = 4) -> list[dict[str, str]]:
        now = self.engine.clock.now()
        out: list[dict[str, str]] = []
        d = now.astimezone(KST).date()
        for _ in range(14):
            if self.engine.cal.is_trading_day(d):
                for t, name, _job in daily_jobs(self.engine, self.interval_minutes):
                    at = datetime.combine(d, t, KST)
                    if at > now:
                        out.append({"name": name, "at": at.isoformat()})
            if len(out) >= n:
                break
            d += timedelta(days=1)
        return out[:n]
