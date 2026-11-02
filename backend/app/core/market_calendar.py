"""국내(KRX) 장 운영 캘린더.

- 정규장 09:00~15:30. 주문 접수는 장 시작 전 08:30~09:00(시가 체결)과 09:00~15:20(종가 체결)만 허용 (D-05)
- 휴장일: 내장 KRX 휴장일 목록 + 사용자 추가 휴장일
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.core.clock import KST

# TODO(verify): 내장 휴장일은 공휴일·대체공휴일 규칙으로 작성한 best-effort 목록이다.
# 실제 휴장일과 다르면 MarketCalendar(extra_holidays=...) 또는 이 목록을 고친다.
KRX_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in [
        # 2026
        "2026-01-01",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-03-02",
        "2026-05-01",
        "2026-05-05",
        "2026-05-25",
        "2026-06-03",
        "2026-08-17",
        "2026-09-24",
        "2026-09-25",
        "2026-10-05",
        "2026-10-09",
        "2026-12-25",
        "2026-12-31",
        # 2027
        "2027-01-01",
        "2027-02-08",
        "2027-02-09",
        "2027-03-01",
        "2027-05-05",
        "2027-05-13",
        "2027-08-16",
        "2027-09-14",
        "2027-09-15",
        "2027-09-16",
        "2027-10-04",
        "2027-10-11",
        "2027-12-27",
        "2027-12-31",
    ]
)

REGULAR_OPEN = time(9, 0)
CONTINUOUS_CLOSE = time(15, 20)  # 종가 단일가 시작
REGULAR_CLOSE = time(15, 30)
PRE_OPEN_WINDOW = timedelta(minutes=30)  # 08:30~09:00 장 시작 전 주문 접수 (시가 체결)
PUBLISH_TIME = time(13, 0)


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    order_cutoff: datetime
    close: datetime


class MarketCalendar:
    def __init__(self, extra_holidays: set[date] | None = None) -> None:
        self._holidays: set[date] = set(KRX_HOLIDAYS) | (extra_holidays or set())

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def session(self, d: date) -> Session | None:
        if not self.is_trading_day(d):
            return None
        return Session(
            day=d,
            open=datetime.combine(d, REGULAR_OPEN, KST),
            order_cutoff=datetime.combine(d, CONTINUOUS_CLOSE, KST),
            close=datetime.combine(d, REGULAR_CLOSE, KST),
        )

    def is_market_open(self, t: datetime) -> bool:
        s = self.session(t.astimezone(KST).date())
        return s is not None and s.open <= t < s.close

    def is_order_allowed(self, t: datetime) -> bool:
        """주문 허용 시각: 거래일의 연속매매 구간."""
        s = self.session(t.astimezone(KST).date())
        return s is not None and s.open <= t < s.order_cutoff

    def next_session(self, t: datetime) -> Session:
        d = t.astimezone(KST).date()
        for i in range(0, 30):
            s = self.session(d + timedelta(days=i))
            if s is not None and s.close > t:
                return s
        raise RuntimeError("30일 안에 거래일이 없습니다")

    def next_trading_day(self, d: date) -> date:
        for i in range(1, 30):
            n = d + timedelta(days=i)
            if self.is_trading_day(n):
                return n
        raise RuntimeError("30일 안에 거래일이 없습니다")

    def previous_trading_day(self, d: date) -> date:
        for i in range(1, 30):
            p = d - timedelta(days=i)
            if self.is_trading_day(p):
                return p
        raise RuntimeError("30일 안에 거래일이 없습니다")

    def trading_days(self, start: date, end: date) -> list[date]:
        out: list[date] = []
        d = start
        while d <= end:
            if self.is_trading_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def publish_time(self, d: date) -> datetime:
        """공공데이터 일봉 공개 시각: 기준일 다음 거래일 13:00 (DATA_API_NOTES §1)."""
        return datetime.combine(self.next_trading_day(d), PUBLISH_TIME, KST)

    def latest_published_day(self, now: datetime) -> date:
        """`now` 시점에 공식 일봉이 공개돼 있는 가장 최근 거래일."""
        d = now.astimezone(KST).date()
        for _ in range(40):
            if self.is_trading_day(d) and self.publish_time(d) <= now:
                return d
            d -= timedelta(days=1)
        raise RuntimeError("공개된 거래일을 찾지 못했습니다")

    def fill_basis(self, t: datetime) -> str | None:
        """주문 접수 시각 → 체결 기준가. OPEN(시가) | CLOSE(종가) | None(접수 불가)."""
        s = self.session(t.astimezone(KST).date())
        if s is None:
            return None
        if s.open - PRE_OPEN_WINDOW <= t < s.open:
            return "OPEN"
        if s.open <= t < s.order_cutoff:
            return "CLOSE"
        return None
