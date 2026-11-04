"""판단 일정 설정 (D-51): 시작·끝·주기 → 자정 기준으로 딱 떨어지는 시각.

규칙: [시작, 끝] (양 끝 포함) 안의 시각 t 중 `자정부터 분 % 주기 == 0` 인 것만. 시각은 그 시장의 현지 시각
(국장 한국 시각, 미장 뉴욕 시각 — 서머타임은 날짜별로 zoneinfo 가 맞춘다).
예) 08:00~17:00 · 60분 → 08:00, 09:00, …, 17:00 / ("08:10", "09:00", 15) → 08:15, 08:30, 08:45, 09:00

저장: 그 스택 kv 의 `schedule` = {start, end, interval_minutes}. 없으면 기본(.env) 일정 — D-51 이전과 같다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import time
from typing import Any

from app.engine.settings_store import SettingsStore

log = logging.getLogger(__name__)

KV_KEY = "schedule"
INTERVALS: tuple[int, ...] = (5, 10, 15, 20, 30, 60, 120, 180, 240)
TIME_STEP = 5  # 시작·끝 시각 단위(분)
MAX_SESSIONS = 96  # 하루 판단 세션 상한
_HHMM = re.compile(r"(\d{1,2}):(\d{2})")


class ScheduleError(ValueError):
    """화면에 그대로 보여 줄 한국어 사유 (API 400)."""


def to_minutes(hhmm: str) -> int:
    m = _HHMM.fullmatch(str(hhmm).strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ScheduleError("시작·끝 시각을 HH:MM 으로 골라 주세요")
    return int(m[1]) * 60 + int(m[2])


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def grid_minutes(start: int, end: int, interval: int) -> list[int]:
    """[start, end] 안에서 interval 로 나누어떨어지는 분(자정 기준)."""
    if interval <= 0 or end < start:
        return []
    first = -(-start // interval) * interval  # 올림
    return list(range(first, end + 1, interval))


def interval_label(minutes: int) -> str:
    """5분 · 15분 · 1시간 · 1시간 30분 (화면 표기와 같다)."""
    if minutes < 60:
        return f"{minutes}분"
    h, m = divmod(minutes, 60)
    return f"{h}시간 {m}분" if m else f"{h}시간"


@dataclass(frozen=True)
class ScheduleConfig:
    start: int  # 자정부터 분
    end: int
    interval: int

    def minutes(self) -> list[int]:
        return grid_minutes(self.start, self.end, self.interval)

    def times(self) -> list[time]:
        return [time(m // 60, m % 60) for m in self.minutes()]

    def as_dict(self) -> dict[str, Any]:
        return {"start": hhmm(self.start), "end": hhmm(self.end), "interval_minutes": self.interval}


def validate(start: str, end: str, interval: int) -> ScheduleConfig:
    """화면 검사(frontend utils/schedule.ts validateSchedule)와 같은 순서·문구."""
    s, e = to_minutes(start), to_minutes(end)
    if s % TIME_STEP or e % TIME_STEP:
        raise ScheduleError(f"시작·끝 시각은 {TIME_STEP}분 단위여야 해요")
    if e <= s:
        raise ScheduleError("끝 시각은 시작 시각보다 늦어야 해요")
    if interval not in INTERVALS:
        allowed = "·".join(interval_label(i) for i in INTERVALS)
        raise ScheduleError(f"고를 수 없는 주기예요 ({allowed} 중 하나)")
    n = len(grid_minutes(s, e, interval))
    if n == 0:
        raise ScheduleError("이 범위에는 판단 시각이 하나도 없어요 — 시작·끝이나 주기를 바꿔 주세요")
    if n > MAX_SESSIONS:
        raise ScheduleError(f"판단 세션이 하루 {n}회로 너무 많아요 (최대 {MAX_SESSIONS}회)")
    return ScheduleConfig(s, e, interval)


def load(kv: SettingsStore) -> ScheduleConfig | None:
    """저장된 일정. 없거나 깨져 있으면 None (= 기본 일정)."""
    raw = kv.get(KV_KEY)
    if not raw:
        return None
    try:
        return validate(str(raw["start"]), str(raw["end"]), int(raw["interval_minutes"]))
    except (KeyError, TypeError, ValueError) as e:
        log.warning("저장된 판단 일정을 쓸 수 없어 기본 일정으로 돌아갑니다: %s", e)
        return None


def save(kv: SettingsStore, cfg: ScheduleConfig) -> None:
    kv.set(KV_KEY, cfg.as_dict())


def clear(kv: SettingsStore) -> None:
    kv.set(KV_KEY, {})
