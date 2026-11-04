"""스케줄러 — 국장·미장 공통 1분 디스패처 (D-48, D-51).

거래일(그 시장의 캘린더 기준)마다 그날 일정을 계산하고, 1분마다 `tick()` 이 지금 시각이 [예정 시각, +10분) 안에 든
작업 중 아직 안 한 것을 한 번 실행한다. 실행 기록은 그 스택 kv `sched_fired` 에 (거래일, 작업 키)로 남겨 재시작해도
같은 작업을 두 번 하지 않는다. 일정 설정(kv `schedule`)은 매 분 다시 읽으므로 바꾸면 재시작 없이 바로 적용된다.

기본 일정(설정 없음 = D-51 이전과 같음):
- 국장: 08:35 장전 판단 + 장중 09:00 + N×AGENT_INTERVAL_MINUTES (15:20 전까지) + 15:45 일일 리포트 (한국 시각 고정)
- 미장: 장 시작 25분 전 장전 판단 + 장 시작 + N×간격 (주문 마감 전까지) + 마감 15분 뒤 리포트 (서머타임 반영)

화면에서 정한 일정(D-51): [시작, 끝] 안의 '딱 떨어지는' 시각(시장 현지 시각)에 판단, 장 시작 전 슬롯은 장전 판단
(`engine.morning`), 나머지는 정시 판단(`engine.hourly`) — 장 시간 밖 슬롯도 판단은 하고 주문은 기존 접수 규칙을 따른다.
리포트는 마감 15분 뒤(국장 15:45). 설정을 바꾼 시각 이전 슬롯은 실행하지 않는다(15:33 에 바꾸면 다음 슬롯부터).

그 밖: 매 1분 지정가·장전 주문 체결·당일 만료·손절/익절 감시(`intraday`, 장 시작 1분 전 ~ 마감 5분 뒤에만),
일봉 갱신(장 시작 1시간 전 ~ 마감 30분 뒤에만 10분마다 — 국장·미장 같음, B6), 하루 한 번 데이터 정리(`maintenance`, B7).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.clock import KST
from app.core.market_calendar import CONTINUOUS_CLOSE, REGULAR_OPEN, Session
from app.engine import decision_schedule as dsched
from app.engine.decision_schedule import ScheduleConfig
from app.engine.engine import TradingEngine
from app.engine.maintenance import run_maintenance
from app.reports.daily import build_daily_report

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[object]]

KR_MORNING = time(8, 35)
KR_REPORT = time(15, 45)
PRE_OPEN_LEAD = timedelta(minutes=25)  # 미장: 장 시작 25분 전 장전 판단 (국장 08:35 와 같은 역할)
REPORT_DELAY = timedelta(minutes=15)  # 마감 15분 뒤 일일 리포트
GRACE = timedelta(minutes=10)  # 늦게 깨어나도 이 안이면 실행 (APScheduler misfire_grace_time 과 같은 역할)
REFRESH_BEFORE = timedelta(
    minutes=60
)  # 일봉 갱신(10분)은 장 시작 1시간 전 ~ 마감 30분 뒤에만 (국장·미장, B6)
REFRESH_AFTER = timedelta(minutes=30)
DECISIONS = ("morning", "hourly")
TZ_LABEL = {"KR": "한국 시각", "US": "미국 동부 시각"}


def session_times(interval_minutes: int) -> list[time]:
    """국장 기본 장중 판단 시각. interval 0 이하이면 14:00 한 번(하루 2회 모드)."""
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


def kr_default_times(interval_minutes: int) -> list[tuple[time, str]]:
    """국장 기본 일정 (한국 시각): 08:35 장전 + 장중 판단 + 15:45 리포트."""
    return [
        (KR_MORNING, "morning"),
        *((t, "hourly") for t in session_times(interval_minutes)),
        (KR_REPORT, "report"),
    ]


def daily_jobs(engine: TradingEngine, interval_minutes: int) -> list[tuple[time, str, Job]]:
    """국장 기본 일정 + 실행할 작업 (예전 cron 일정과 같다)."""

    async def report() -> None:
        await build_daily_report(engine)

    fn: dict[str, Job] = {"morning": engine.morning, "hourly": engine.hourly, "report": report}
    return [(t, name, fn[name]) for t, name in kr_default_times(interval_minutes)]


def session_job_times(s: Session, interval_minutes: int) -> list[tuple[datetime, str]]:
    """미장 기본 일정(시각은 tz-aware): 장전 판단, 장중 판단(장 시작 + N×간격, 주문 마감 전까지), 일일 리포트."""
    out: list[tuple[datetime, str]] = [(s.open - PRE_OPEN_LEAD, "morning")]
    if interval_minutes <= 0:
        out.append((s.open + (s.order_cutoff - s.open) / 2, "hourly"))
    else:
        t = s.open + timedelta(minutes=interval_minutes)
        while t < s.order_cutoff:
            out.append((t, "hourly"))
            t += timedelta(minutes=interval_minutes)
    out.append((s.close + REPORT_DELAY, "report"))
    return out


def custom_job_times(s: Session, cfg: ScheduleConfig, tz: ZoneInfo) -> list[tuple[datetime, str]]:
    """화면에서 정한 일정(D-51): 그 거래일의 격자 시각(현지 시각) + 마감 15분 뒤 리포트.

    장 시작 전 슬롯은 장전 판단(캘린더 갱신·시초 자산 기록 포함), 그 뒤는 정시 판단.
    같은 시각의 판단과 리포트는 판단이 먼저(리포트가 그 판단 결과까지 담도록).
    """
    out: list[tuple[datetime, str]] = []
    for t in cfg.times():
        at = datetime.combine(s.day, t, tz)
        out.append((at, "morning" if at < s.open else "hourly"))
    out.append((s.close + REPORT_DELAY, "report"))
    out.sort(key=lambda x: x[0])  # 안정 정렬: 같은 시각이면 판단이 리포트보다 앞
    return out


class Scheduler:
    """국장 스케줄러 (그리고 미장 `SessionScheduler` 의 바탕). 1분 디스패처 방식 (D-51)."""

    KEY = "sched_fired"
    REFRESH_IN_TICK = False  # 국장: 일봉 갱신은 10분 간격 작업으로 따로 (장 앞뒤에만 실제로 갱신)
    MAINTENANCE_AT = time(4, 40)  # 하루 한 번 데이터 정리 (한국 시각, 국장 장외)

    def __init__(self, engine: TradingEngine, interval_minutes: int = 60) -> None:
        self.engine = engine
        self.default_interval = interval_minutes  # .env 의 장중 판단 간격 (기본 일정)
        self._sched: AsyncIOScheduler | None = None
        self._busy = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._calendar_day: date | None = None

    # ------------------------------------------------------------ 설정
    @property
    def market_code(self) -> str:
        return self.engine.market.code if self.engine.market else "KR"

    @property
    def tz(self) -> ZoneInfo:
        return self.engine.cal.tz

    def config(self) -> ScheduleConfig | None:
        """화면에서 정한 일정 (없으면 None = 기본 일정). 매번 kv 에서 읽는다."""
        return dsched.load(self.engine.settings)

    @property
    def interval_minutes(self) -> int:
        cfg = self.config()
        return cfg.interval if cfg is not None else self.default_interval

    # ------------------------------------------------------------ 일정 계산
    def default_jobs(self, d: date) -> list[tuple[datetime, str]]:
        """기본 일정 — 국장: 거래일이면 08:35 · 장중 · 15:45 (한국 시각 고정, 예전 cron 과 같음)."""
        if not self.engine.cal.is_trading_day(d):
            return []
        return [(datetime.combine(d, t, KST), name) for t, name in kr_default_times(self.default_interval)]

    def plan(self, d: date, cfg: ScheduleConfig | None) -> list[tuple[datetime, str]]:
        """거래일 d(시장 현지 날짜)의 일정. 휴장일이면 []."""
        if cfg is None:
            return self.default_jobs(d)
        s = self.engine.cal.session(d)
        return custom_job_times(s, cfg, self.tz) if s is not None else []

    def jobs_for(self, d: date) -> list[tuple[datetime, str]]:
        return self.plan(d, self.config())

    def _candidates(self, now: datetime, cfg: ScheduleConfig | None) -> list[tuple[date, datetime, str]]:
        today = self.engine.cal.local_date(now)
        out: list[tuple[date, datetime, str]] = []
        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            out += [(d, at, name) for at, name in self.plan(d, cfg)]
        return sorted(out, key=lambda x: x[1])

    @staticmethod
    def _key(d: date, at: datetime, name: str) -> str:
        """작업 키 = '거래일 HH:MM 이름' (HH:MM 은 한국 시각)."""
        return f"{d.isoformat()} {at.astimezone(KST):%H:%M} {name}"

    # ------------------------------------------------------------ 디스패처
    def due(self, now: datetime) -> list[tuple[str, str]]:
        """지금 실행할 (작업 키, 이름)."""
        fired = self.engine.settings.get(self.KEY)
        out = []
        for d, at, name in self._candidates(now, self.config()):
            key = self._key(d, at, name)
            if at <= now < at + GRACE and key not in fired.get(d.isoformat(), []):
                out.append((key, name))
        return out

    def _mark(self, *keys: str) -> None:
        if not keys:
            return
        fired = self.engine.settings.get(self.KEY)
        latest = max(date.fromisoformat(k.split(" ", 1)[0]) for k in keys)
        keep = {d: list(v) for d, v in fired.items() if d >= (latest - timedelta(days=3)).isoformat()}
        for key in keys:
            day = key.split(" ", 1)[0]
            if key not in keep.setdefault(day, []):
                keep[day].append(key)
        self.engine.settings.set(self.KEY, keep)

    def skip_passed(self) -> list[str]:
        """일정을 바꾼 직후: 지금 시각까지의 판단 슬롯은 실행한 것으로 친다.

        예) 15:33 에 30분 간격으로 바꾸면 15:30 슬롯(유예 10분 안)을 늦게 돌리지 않고 다음 슬롯(16:00)부터.
        리포트는 일정과 관계없이 그대로 둔다.
        """
        now = self.engine.clock.now()
        keys = [
            self._key(d, at, name)
            for d, at, name in self._candidates(now, self.config())
            if at <= now and name in DECISIONS
        ]
        self._mark(*keys)
        return keys

    def _job(self, name: str) -> Job:
        if name == "morning":
            return self.engine.morning
        if name == "hourly":
            return self.engine.hourly

        async def report() -> None:
            await build_daily_report(self.engine)

        return report

    async def _daily_calendar(self, now: datetime) -> None:
        """현지 날짜가 바뀌면 하루 한 번 토스 장 운영 캘린더를 반영한다 (장전 슬롯이 없는 일정에서도 휴장일을 맞게)."""
        d = self.engine.cal.local_date(now)
        if self._calendar_day == d:
            return
        self._calendar_day = d
        try:
            await self.engine.refresh_calendar()
        except Exception:
            log.exception("장 운영 캘린더 갱신 실패")

    async def tick(self, wait: bool = False) -> list[str]:
        """1분 디스패처. 시작한 작업 키 목록 (테스트·로그용).

        판단 세션은 몇 분 걸리므로 백그라운드 작업으로 띄워 디스패처가 막히지 않게 한다. 판단은 다른 작업이 진행 중이면
        건너뛰고(`_busy`), 리포트는 진행 중인 판단이 끝나기를 기다렸다가 만든다. wait=True 이면 끝날 때까지 기다린다(테스트).
        """
        now = self.engine.clock.now()
        await self._daily_calendar(now)
        ran: list[str] = []
        runs: list[Awaitable[None]] = []
        for key, name in self.due(now):
            self._mark(key)  # 먼저 기록: 실패해도 같은 작업을 1분마다 다시 하지 않는다
            ran.append(key)
            runs.append(self._guarded(name, self._job(name), busy_wait=name == "report")())
        if self.REFRESH_IN_TICK and self._near_session(now) and now.minute % 10 == 0:
            runs.append(self._guarded("refresh", self.engine.sync_data)())
        for coro in runs:
            if wait:
                await coro
            else:
                t: asyncio.Task[None] = asyncio.ensure_future(coro)
                self._tasks.add(t)
                t.add_done_callback(self._tasks.discard)
        return ran

    async def refresh(self) -> None:
        """10분 일봉 갱신 — 장 시작 1시간 전 ~ 마감 30분 뒤에만 (밖에서는 일봉·자산이 바뀌지 않는다)."""
        if self._near_session(self.engine.clock.now()):
            await self.engine.sync_data()

    async def maintenance(self) -> dict[str, int]:
        """하루 한 번 데이터 정리 (B7): 만료된 API 캐시, 모의계좌 JSON 의 오래된 끝난 주문.

        이벤트 루프에서 바로 돈다(모의계좌 상태를 주문 처리와 번갈아 고치지 않게). 몇 ms 짜리 작업이다.
        """
        e = self.engine
        return run_maintenance(e.db, e.clock.now(), e.broker)

    def _near_session(self, now: datetime) -> bool:
        cal = self.engine.cal
        d = cal.local_date(now)
        for day in (d - timedelta(days=1), d):
            s = cal.session(day)
            if s is not None and s.open - REFRESH_BEFORE <= now <= s.close + REFRESH_AFTER:
                return True
        return False

    def _guarded(
        self, name: str, job: Job, *, busy_skip: bool = True, busy_wait: bool = False
    ) -> Callable[[], Awaitable[None]]:
        """busy_skip: 다른 작업 중이면 건너뜀 / busy_wait: 끝나기를 기다렸다가 실행 / 둘 다 아니면 잠금 없이."""

        async def run() -> None:
            if busy_skip and not busy_wait and self._busy.locked():
                log.info("작업 %s 건너뜀(다른 작업 실행 중)", name)
                return
            try:
                if busy_skip or busy_wait:
                    async with self._busy:
                        await job()
                else:
                    await job()
            except Exception:
                log.exception("스케줄 작업 실패: %s", name)
                self.engine.hub.audit("ERROR", f"스케줄 작업 실패: {name}", None)

        return run

    def start(self) -> None:
        self._sched = AsyncIOScheduler(timezone=KST)
        self._sched.add_job(
            self.tick,
            "cron",
            second=1,  # 매 분 1초 — 판단 시각(정각 분)에서 1초 안에 시작
            id="dispatch",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=50,
        )
        self._sched.add_job(
            self._guarded("intraday", self.engine.intraday, busy_skip=False),
            "interval",
            minutes=1,
            id="intraday",
            coalesce=True,
            max_instances=1,
        )
        if not self.REFRESH_IN_TICK:
            self._sched.add_job(
                self._guarded("refresh", self.refresh),
                "interval",
                minutes=10,
                id="refresh",
                coalesce=True,
                max_instances=1,
            )
        self._sched.add_job(
            self._guarded("maintenance", self.maintenance, busy_skip=False),
            "cron",
            hour=self.MAINTENANCE_AT.hour,
            minute=self.MAINTENANCE_AT.minute,
            id="maintenance",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        self._sched.start()
        nxt = ", ".join(f"{r['name']}@{r['at'][5:16]}" for r in self.next_runs(6))
        log.info("스케줄러 시작(%s) — %s / 다음 일정(KST): %s", self.market_code, self.describe(), nxt)

    def shutdown(self) -> None:
        if self._sched:
            self._sched.shutdown(wait=False)

    # ------------------------------------------------------------ 조회
    def next_runs(self, n: int = 4) -> list[dict[str, str]]:
        """다음 일정 n개 (시각은 한국 시각 ISO)."""
        now = self.engine.clock.now()
        cfg = self.config()
        d = self.engine.cal.local_date(now) - timedelta(days=1)
        found: list[tuple[datetime, str]] = []
        for _ in range(16):
            found += [(at, name) for at, name in self.plan(d, cfg) if at > now]
            if len(found) >= n:
                break
            d += timedelta(days=1)
        found.sort(key=lambda x: x[0])
        return [{"name": name, "at": at.astimezone(KST).isoformat()} for at, name in found[:n]]

    def next_decision(self) -> datetime | None:
        return next(
            (datetime.fromisoformat(r["at"]) for r in self.next_runs(8) if r["name"] in DECISIONS), None
        )

    def _reference(self) -> tuple[Session, list[tuple[datetime, str]]]:
        """보여 줄 기준 거래일(오늘 장이 안 끝났으면 오늘, 아니면 다음 거래일)과 그날 일정."""
        s = self.engine.cal.next_session(self.engine.clock.now())
        return s, self.plan(s.day, self.config())

    def sessions_per_day(self) -> int:
        """하루 판단 세션 수 (비용 예상용)."""
        _s, jobs = self._reference()
        return sum(1 for _at, name in jobs if name in DECISIONS)

    def describe(self) -> str:
        """기동 안내·로그용 한 줄."""
        cfg = self.config()
        s, jobs = self._reference()
        times = [f"{at.astimezone(self.tz):%H:%M}" for at, name in jobs if name in DECISIONS]
        label = TZ_LABEL.get(self.market_code, "")
        if cfg is not None:
            a, b = dsched.hhmm(cfg.start), dsched.hhmm(cfg.end)
            return f"판단 {a}~{b} {dsched.interval_label(cfg.interval)}마다 {len(times)}회({label})"
        span = f"{times[0]}~{times[-1]} " if times else ""
        return f"판단 기본 일정 {span}{len(times)}회({label}, 장중 {self.default_interval}분 간격)"

    def schedule_info(self) -> dict[str, Any]:
        """에이전트 구조 화면용: 다음(또는 오늘) 거래일 판단 시각 — 한국 시각.

        morning = 그날 첫 판단, sessions = 나머지 판단 (화면은 첫 판단을 '장전'으로 표시한다).
        """
        s, jobs = self._reference()
        dec = [(at.astimezone(KST), name) for at, name in jobs if name in DECISIONS]
        rep = next((at for at, name in jobs if name == "report"), s.close + REPORT_DELAY)
        first = next((i for i, (_t, nm) in enumerate(dec) if nm == "morning"), 0)
        times = [f"{t:%H:%M}" for t, _nm in dec]
        return {
            "day": s.day.isoformat(),
            "custom": self.config() is not None,
            "timezone": "Asia/Seoul",
            "morning": times[first] if times else "",
            "sessions": [t for i, t in enumerate(times) if i != first],
            "times": times,
            "report": f"{rep.astimezone(KST):%H:%M}",
        }

    def view(self) -> dict[str, Any]:
        """`GET /api/schedule` 응답 (시각은 시장 현지 시각, times_kst 는 같은 세션의 한국 시각)."""
        cal = self.engine.cal
        tz = self.tz
        cfg = self.config()
        s, jobs = self._reference()
        dec = [at for at, name in jobs if name in DECISIONS]
        rep = next((at for at, name in jobs if name == "report"), s.close + REPORT_DELAY)
        default = self.default_view(s)
        lo = s.open - cal.pre_open_window

        def hm(t: datetime) -> str:
            return f"{t.astimezone(tz):%H:%M}"

        return {
            "market": self.market_code,
            "timezone": str(tz),
            "tz_label": TZ_LABEL.get(self.market_code, str(tz)),
            "custom": cfg is not None,
            **(cfg.as_dict() if cfg is not None else default),
            "default": default,
            "intervals": list(dsched.INTERVALS),
            "max_sessions": dsched.MAX_SESSIONS,
            "day": s.day.isoformat(),
            "times": [hm(t) for t in dec],
            "times_kst": [f"{t.astimezone(KST):%H:%M}" for t in dec],
            "market_hours": {"open": hm(s.open), "close": hm(s.close), "order_cutoff": hm(s.order_cutoff)},
            # 화면 규칙과 같다: (장 시작 - 장전 접수 30분)보다 이르거나 주문 마감보다 늦은 시각
            "outside_market": [
                hm(t) for t in dec if t < lo or t >= s.order_cutoff
            ],  # 마감 시각부터는 주문 접수 안 됨(fill_basis)
            "report_at": hm(rep),
            "next_runs": self.next_runs(6),
        }

    def default_view(self, s: Session | None = None) -> dict[str, Any]:
        """기본 일정(설정 없음)을 시작·끝·주기로 나타낸 것 — '기본값으로' 가 되돌릴 값."""
        s = s or self.engine.cal.next_session(self.engine.clock.now())
        times = [at for at, name in self.default_jobs(s.day) if name in DECISIONS]
        first = f"{times[0].astimezone(self.tz):%H:%M}" if times else ""
        last = f"{times[-1].astimezone(self.tz):%H:%M}" if times else ""
        return {"start": first, "end": last, "interval_minutes": self.default_interval}


class SessionScheduler(Scheduler):
    """미장 스케줄러: 기본 일정을 시장 세션(캘린더)에서 매일 계산한다 (정규장이 한국 시각 자정을 넘긴다, D-48)."""

    REFRESH_IN_TICK = True  # 일봉 갱신은 장 시작 1시간 전 ~ 마감 30분 뒤에만
    MAINTENANCE_AT = time(12, 40)  # 한국 시각 낮 = 미국 장외

    def default_jobs(self, d: date) -> list[tuple[datetime, str]]:
        s = self.engine.cal.session(d)
        return session_job_times(s, self.default_interval) if s is not None else []
