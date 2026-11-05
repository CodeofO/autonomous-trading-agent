"""차트 봉 (D-63): 토스 1분봉·일봉을 5·15·30·60분, 주·월 봉으로 모으기, 봉 시각 규칙(종목 1분봉 = 종료 시각,
지표 = 시작 시각), 페이지 이어 받기, 지표 차트 API(환율 없음·국채 분봉 없음). 네트워크 없음."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.core.clock import KST
from app.market import chart as ch
from tests.test_api import _client

NY = ZoneInfo("America/New_York")


def wall(y: int, mo: int, d: int, h: int, mi: int) -> int:
    """한국 시각 벽시계를 UTC 인 것처럼 센 초 (차트가 한국 시각으로 보이게)."""
    return int(datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp())


def minute_candles(start: datetime, n: int, *, end_stamped: bool) -> list[dict[str, Any]]:
    """1분봉 n개 (토스처럼 최신순, 값은 문자열). 종가 = 100 + i, 거래량 = 10."""
    rows = []
    for i in range(n):
        t = start + timedelta(minutes=i + (1 if end_stamped else 0))
        rows.append(
            {
                "timestamp": t.isoformat(),
                "openPrice": str(100 + i - 0.5),
                "highPrice": str(100 + i + 1),
                "lowPrice": str(100 + i - 1),
                "closePrice": str(100 + i),
                "volume": "10",
            }
        )
    return rows[::-1]


def test_stock_minute_bars_use_end_time_and_group_into_five_minutes() -> None:
    raws = ch.parse(
        minute_candles(datetime(2026, 11, 5, 9, 0, tzinfo=KST), 10, end_stamped=True), end_stamped=True
    )
    assert raws[0].start == datetime(2026, 11, 5, 9, 0, tzinfo=KST)  # 09:01 종료 봉 = 09:00 봉
    bars = ch.aggregate(raws, ch.get("5m"), KST)
    assert bars == [
        {
            "time": wall(2026, 11, 5, 9, 0),
            "open": 99.5,
            "high": 105.0,
            "low": 99.0,
            "close": 104.0,
            "volume": 50.0,
        },
        {
            "time": wall(2026, 11, 5, 9, 5),
            "open": 104.5,
            "high": 110.0,
            "low": 104.0,
            "close": 109.0,
            "volume": 50.0,
        },
    ]


def test_index_minute_bars_use_start_time_and_hour_bars_align_to_clock() -> None:
    raws = ch.parse(
        minute_candles(datetime(2026, 11, 5, 9, 50, tzinfo=KST), 20, end_stamped=False), end_stamped=False
    )
    bars = ch.aggregate(raws, ch.get("60m"), KST)
    assert [b["time"] for b in bars] == [wall(2026, 11, 5, 9, 0), wall(2026, 11, 5, 10, 0)]
    assert bars[0]["volume"] == 100.0 and bars[1]["close"] == 119.0


def test_us_daily_bars_become_weeks_and_months_by_new_york_date() -> None:
    # 미장 일봉 timestamp = 뉴욕 자정을 한국 시각으로 (예: 11/03 뉴욕 = 11/03 14:00 KST, 표준시)
    days = [datetime(2026, 10, 26, tzinfo=NY) + timedelta(days=i) for i in range(10) if (i % 7) < 5]
    rows = [
        {"timestamp": d.astimezone(KST).isoformat(), "closePrice": str(10 + i), "volume": "1"}
        for i, d in enumerate(days)
    ][::-1]
    raws = ch.parse(rows, end_stamped=False)
    weeks = ch.aggregate(raws, ch.get("1w"), NY)
    assert [w["time"] for w in weeks] == ["2026-10-26", "2026-11-02"]
    assert weeks[0]["open"] == 10.0 and weeks[0]["close"] == 14.0 and weeks[0]["volume"] == 5.0
    months = ch.aggregate(raws, ch.get("1M"), NY)
    assert [m["time"] for m in months] == ["2026-10-01", "2026-11-01"]
    assert ch.aggregate(raws, ch.get("1d"), NY)[0]["time"] == "2026-10-26"


async def test_collect_follows_next_before_until_enough_or_page_cap() -> None:
    calls: list[datetime | None] = []

    async def fetch(before: datetime | None) -> ch.Page:
        calls.append(before)
        n = len(calls)
        return [{"i": n}] * 200, (datetime(2026, 11, 1, 9, 0, tzinfo=KST) - timedelta(days=n - 1)).isoformat()

    rows = await ch.collect(fetch, 450)
    assert len(rows) == 600 and calls[0] is None and calls[1] == datetime(2026, 11, 1, 9, 0, tzinfo=KST)
    calls.clear()
    assert len(await ch.collect(fetch, 10_000)) == 200 * ch.MAX_PAGES  # 상한

    async def last_page(before: datetime | None) -> ch.Page:
        return [{"x": 1}], None

    assert len(await ch.collect(last_page, 500)) == 1


def test_unknown_interval_and_bond_has_no_minutes() -> None:
    with pytest.raises(ch.ChartError):
        ch.get("2h")
    assert all(x["enabled"] == (not x["intraday"]) for x in ch.catalog(minutes=False))


def test_chart_api(tmp_path: Path) -> None:
    ch._cache.clear()
    with _client(tmp_path, datetime(2026, 11, 4, 12, 0, tzinfo=KST), auth_enabled=False) as c:
        r = c.get("/api/market/indicators/USDKRW/chart")
        assert r.status_code == 404 and "환율 차트" in r.json()["detail"]
        assert c.get("/api/market/indicators/KR_BOND_10Y/chart?interval=5m").status_code == 400
        kospi = c.get("/api/market/indicators/KOSPI/chart?interval=1d").json()
        assert kospi["intraday"] is False and len(kospi["bars"]) > 5 and kospi["key"] == "KOSPI"
        assert c.get("/api/market/indicators/NIKKEI/chart").status_code == 404
        st = c.get("/api/market/005930/chart?interval=1w").json()
        assert st["interval"] == "1w" and st["bars"] and st["currency"] == "KRW"
        assert [x["key"] for x in st["intervals"]] == ["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1M"]
        assert c.get("/api/market/005930/chart?interval=2h").status_code == 400
        us = c.get("/api/us/market/AAPL/chart?interval=1d")
        assert us.status_code in (200, 503)
