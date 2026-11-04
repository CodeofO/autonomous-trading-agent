"""국내(KRX) 장 운영 캘린더.

- 정규장 09:00~15:30. 주문 접수는 장 시작 전 08:30~09:00(시가 체결)과 09:00~15:20(종가 체결)만 허용 (D-05)
- 휴장일: 토스 장 운영 캘린더(키가 있으면 우선) → 내장 KRX 휴장일 목록 + 사용자 추가 휴장일
- 장외 세션(D-57): 토스 장 운영 캘린더의 프리마켓·애프터마켓(KRX+NXT 통합) 시각을 날짜마다 그대로 쓴다.
  설정에서 켠 세션에서만 주문을 받는다. 토스 캘린더를 못 받은 날은 장외 세션이 없는 것으로 본다(추측하지 않음).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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


@dataclass(frozen=True)
class ExtSession:
    """장외 세션 (토스 장 운영 캘린더). 시각은 tz-aware — 서머타임은 토스가 날짜마다 준 시각에 이미 들어 있다."""

    name: str  # day_market | pre_market | after_market
    day: date  # 이 세션이 속한 거래일 (토스 캘린더의 date, 그 시장 현지 날짜)
    start: datetime
    end: datetime


EXT_LABEL = {"day_market": "데이마켓", "pre_market": "프리마켓", "after_market": "애프터마켓"}
EXT_KEEP_DAYS = 10  # 지난 장외 세션 기록은 이만큼만 남긴다


def toss_time(v: Any) -> datetime | None:
    return datetime.fromisoformat(v).astimezone(KST) if v else None


class MarketCalendar:
    """국내 장 캘린더. 미국은 `app.core.us_calendar.UsMarketCalendar`(같은 공개 인터페이스).

    거래일(`Session.day`)은 그 시장의 현지 날짜다(`local_date`). 국내는 KST 날짜와 같다.
    """

    tz: ZoneInfo = KST
    pre_open_window: timedelta = PRE_OPEN_WINDOW
    #: 설정에서 켜고 끌 수 있는 장외 세션 (국장: NXT 프리·애프터마켓)
    EXT_SESSIONS: tuple[str, ...] = ("pre_market", "after_market")

    def __init__(self, extra_holidays: set[date] | None = None) -> None:
        self._holidays: set[date] = set(KRX_HOLIDAYS) | (extra_holidays or set())
        self._overrides: dict[date, Session | None] = {}  # 토스 장 운영 캘린더 (우선 적용)
        self._ext: dict[date, list[ExtSession]] = {}  # 토스 장 운영 캘린더의 장외 세션 (D-57)
        self.extended_enabled: frozenset[str] = frozenset()  # 설정에서 켠 장외 세션

    def local_date(self, t: datetime) -> date:
        """`t` 시점의 현지 날짜(거래일 기준 날짜). 국내 = KST 날짜, 미국 = 뉴욕 날짜."""
        return t.astimezone(self.tz).date()

    def is_trading_day(self, d: date) -> bool:
        if d in self._overrides:
            return self._overrides[d] is not None
        return d.weekday() < 5 and d not in self._holidays

    def session(self, d: date) -> Session | None:
        if d in self._overrides:
            return self._overrides[d]
        if not self.is_trading_day(d):
            return None
        return Session(
            day=d,
            open=datetime.combine(d, REGULAR_OPEN, KST),
            order_cutoff=datetime.combine(d, CONTINUOUS_CLOSE, KST),
            close=datetime.combine(d, REGULAR_CLOSE, KST),
        )

    def is_market_open(self, t: datetime) -> bool:
        s = self.session(self.local_date(t))
        return s is not None and s.open <= t < s.close

    def is_order_allowed(self, t: datetime) -> bool:
        """주문 허용 시각: 거래일의 연속매매 구간."""
        s = self.session(self.local_date(t))
        return s is not None and s.open <= t < s.order_cutoff

    def next_session(self, t: datetime) -> Session:
        d = self.local_date(t)
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
        d = self.local_date(now)
        for _ in range(40):
            if self.is_trading_day(d) and self.publish_time(d) <= now:
                return d
            d -= timedelta(days=1)
        raise RuntimeError("공개된 거래일을 찾지 못했습니다")

    def fill_basis(self, t: datetime) -> str | None:
        """주문 접수 시각 → 체결 기준. CLOSE(정규장, 현재가) | EXT(켠 장외 세션) | OPEN(장전 접수 → 시가) | None.

        정규장이 먼저, 그다음 켠 장외 세션, 그다음 장 시작 전 접수 구간 순서다 (프리마켓을 켜면 장전 구간과 겹치는
        시간은 프리마켓 시세로 바로 체결한다).
        """
        s = self.session(self.local_date(t))
        if s is not None and s.open <= t < s.order_cutoff:
            return "CLOSE"
        if self.extended_at(t) is not None:
            return "EXT"
        if s is not None and s.open - self.pre_open_window <= t < s.open:
            return "OPEN"
        return None

    # ------------------------------------------------------------ 장외 세션 (D-57)
    def extended_sessions(self) -> list[ExtSession]:
        """알고 있는 장외 세션 전체 (켜고 끈 것과 관계없이), 시작 순."""
        return sorted((x for xs in self._ext.values() for x in xs), key=lambda x: x.start)

    def extended_at(
        self, t: datetime, *, enabled_only: bool = True, slack: timedelta = timedelta(0)
    ) -> ExtSession | None:
        """`t` 가 들어가는 장외 세션 (기본은 켠 세션만). slack 만큼 끝을 늘려 본다(마감 직후 정리용)."""
        for x in self.extended_sessions():
            if x.start <= t < x.end + slack and (not enabled_only or x.name in self.extended_enabled):
                return x
        return None

    def _set_extended(self, d: date, sessions: list[ExtSession]) -> None:
        self._ext[d] = sessions
        for old in [k for k in self._ext if k < d - timedelta(days=EXT_KEEP_DAYS)]:
            del self._ext[old]

    def order_hours_text(self) -> str:
        """주문 접수 시간 안내 (리스크 거부 사유·화면)."""
        base = "08:30~15:20 거래일만 접수"
        return base + self._ext_hours_suffix()

    def _ext_hours_suffix(self) -> str:
        on = [EXT_LABEL[k] for k in self.EXT_SESSIONS if k in self.extended_enabled]
        return f" + 장외 {'·'.join(on)}(토스 장 운영 시각)" if on else ""

    def apply_toss_calendar(self, result: dict[str, Any]) -> None:
        """토스 `GET /api/v1/market-calendar/KR` 의 result 반영. integrated=null 이면 휴장."""
        for key in ("previousBusinessDay", "today", "nextBusinessDay"):
            entry = result.get(key)
            if not entry:
                continue
            d = date.fromisoformat(entry["date"])
            integrated = entry.get("integrated")
            self._set_extended(d, self._kr_extended(d, integrated or {}))
            regular = integrated.get("regularMarket") if integrated else None
            if not regular:
                self._overrides[d] = None
                continue
            open_ = datetime.fromisoformat(regular["startTime"]).astimezone(KST)
            close = datetime.fromisoformat(regular["endTime"]).astimezone(KST)
            cutoff_raw = regular.get("singlePriceAuctionStartTime")
            cutoff = datetime.fromisoformat(cutoff_raw).astimezone(KST) if cutoff_raw else close
            self._overrides[d] = Session(day=d, open=open_, order_cutoff=cutoff, close=close)

    @staticmethod
    def _kr_extended(d: date, integrated: dict[str, Any]) -> list[ExtSession]:
        """국장 장외 세션: 연속매매 구간만 쓴다.

        - 프리마켓: startTime ~ singlePriceAuctionStartTime(NXT 접속매매 끝, 그 뒤는 시가단일가). 없으면 endTime
        - 애프터마켓: singlePriceAuctionEndTime(NXT 단일가 끝, 접속매매 시작) 또는 startTime ~ endTime
        """
        out: list[ExtSession] = []
        pre = integrated.get("preMarket")
        if pre and (start := toss_time(pre.get("startTime"))):
            end = toss_time(pre.get("singlePriceAuctionStartTime")) or toss_time(pre.get("endTime"))
            if end and end > start:
                out.append(ExtSession("pre_market", d, start, end))
        after = integrated.get("afterMarket")
        if after:
            start = toss_time(after.get("singlePriceAuctionEndTime")) or toss_time(after.get("startTime"))
            end = toss_time(after.get("endTime"))
            if start and end and end > start:
                out.append(ExtSession("after_market", d, start, end))
        return out
