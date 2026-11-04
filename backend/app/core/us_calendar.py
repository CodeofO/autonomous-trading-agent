"""미국(NYSE·NASDAQ) 장 운영 캘린더 — `MarketCalendar` 와 같은 공개 인터페이스 (D-48).

- 거래일 = **미국 현지(뉴욕) 날짜**. 정규장 09:30~16:00 America/New_York (zoneinfo 로 서머타임 자동 반영).
  한국 시각으로는 자정을 넘긴다: 표준시(EST) 23:30~06:00 KST, 서머타임(EDT) 22:30~05:00 KST.
- 주문 접수: 장 시작 전 30분(시가 체결 대기, OPEN) + 정규장 시작~마감 10분 전(현재가 체결, CLOSE). 그 밖은 거부.
- 휴장·단축장: 아래 내장 목록(NYSE 공식) → 토스 `GET /api/v1/market-calendar/US` 가 주는 전일/당일/익일이 우선.

휴장일·단축장 출처(2026-11-04 확인): https://www.nyse.com/markets/hours-calendars
  "Holidays & Trading Hours" 표 — 2026·2027 휴장일과 13:00 조기 마감일.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.clock import KST
from app.core.market_calendar import MarketCalendar, Session

NY = ZoneInfo("America/New_York")

# https://www.nyse.com/markets/hours-calendars (2026-11-04 확인)
NYSE_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in [
        # 2026
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # Martin Luther King, Jr. Day
        "2026-02-16",  # Washington's Birthday
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving Day
        "2026-12-25",  # Christmas Day
        # 2027
        "2027-01-01",  # New Year's Day
        "2027-01-18",  # Martin Luther King, Jr. Day
        "2027-02-15",  # Washington's Birthday
        "2027-03-26",  # Good Friday
        "2027-05-31",  # Memorial Day
        "2027-06-18",  # Juneteenth (observed)
        "2027-07-05",  # Independence Day (observed)
        "2027-09-06",  # Labor Day
        "2027-11-25",  # Thanksgiving Day
        "2027-12-24",  # Christmas Day (observed)
    ]
)
# 13:00 (뉴욕) 조기 마감 — 같은 출처
NYSE_EARLY_CLOSES: frozenset[date] = frozenset(
    date.fromisoformat(d)
    for d in [
        "2026-11-27",  # day after Thanksgiving
        "2026-12-24",  # Christmas Eve
        "2027-11-26",  # day after Thanksgiving
    ]
)

US_OPEN = time(9, 30)
US_CLOSE = time(16, 0)
US_EARLY_CLOSE = time(13, 0)
US_ORDER_CUTOFF = timedelta(minutes=10)  # 마감 10분 전까지 접수 (KR 의 15:20 마감과 같은 역할)
US_PRE_OPEN_WINDOW = timedelta(minutes=30)  # 09:00~09:30 뉴욕 시각 장전 접수 (시가 체결 대기)
US_PUBLISH_DELAY = timedelta(minutes=30)  # 일봉(정규장 OHLC)이 확정됐다고 보는 시각: 마감 30분 뒤


class UsMarketCalendar(MarketCalendar):
    tz = NY
    pre_open_window = US_PRE_OPEN_WINDOW

    def __init__(self, extra_holidays: set[date] | None = None) -> None:
        super().__init__()
        self._holidays = set(NYSE_HOLIDAYS) | (extra_holidays or set())
        self._early = set(NYSE_EARLY_CLOSES)

    def session(self, d: date) -> Session | None:
        if d in self._overrides:
            return self._overrides[d]
        if not self.is_trading_day(d):
            return None
        open_ = datetime.combine(d, US_OPEN, NY)
        close = datetime.combine(d, US_EARLY_CLOSE if d in self._early else US_CLOSE, NY)
        return Session(
            day=d,
            open=open_.astimezone(KST),
            order_cutoff=(close - US_ORDER_CUTOFF).astimezone(KST),
            close=close.astimezone(KST),
        )

    def publish_time(self, d: date) -> datetime:
        """일봉 확정 시각: 그 거래일 정규장 마감 + 30분 (토스 1d 봉 = 정규장 OHLC)."""
        s = self.session(d)
        close = s.close if s else datetime.combine(d, US_CLOSE, NY).astimezone(KST)
        return close + US_PUBLISH_DELAY

    def apply_toss_calendar(self, result: dict[str, Any]) -> None:
        """토스 `GET /api/v1/market-calendar/US` 의 result 반영.

        각 날짜 `{date(미국 현지), dayMarket, preMarket, regularMarket, afterMarket}` — 휴장이면 4개 모두 null.
        조기 마감 표시는 따로 없고 regularMarket.endTime 에 반영된다고 보고 그대로 쓴다.
        """
        for key in ("previousBusinessDay", "today", "nextBusinessDay"):
            entry = result.get(key)
            if not entry or not entry.get("date"):
                continue
            d = date.fromisoformat(entry["date"])
            regular = entry.get("regularMarket")
            if not regular:
                self._overrides[d] = None
                continue
            open_ = datetime.fromisoformat(regular["startTime"]).astimezone(KST)
            close = datetime.fromisoformat(regular["endTime"]).astimezone(KST)
            self._overrides[d] = Session(day=d, open=open_, order_cutoff=close - US_ORDER_CUTOFF, close=close)
