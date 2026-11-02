"""시계 추상화. 실제 시각과 시뮬레이션(가속) 시각을 같은 인터페이스로 다룬다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class Clock(Protocol):
    def now(self) -> datetime: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(KST)


class SimulatedClock:
    """수동으로 전진시키는 시계. 백테스트·시뮬레이션·테스트에서 사용."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=KST)
        self._now = start.astimezone(KST)

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        self._now = t.astimezone(KST)

    def advance(self, delta: timedelta) -> datetime:
        self._now = self._now + delta
        return self._now
