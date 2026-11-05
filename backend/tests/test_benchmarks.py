"""주요 지표 하루 종가 기록 (D-72): 일봉 → 시장 날짜, 처음 200일 채우기, 덮어쓰기, 환율은 16시 슬롯만 (실제 호출 없음)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from app.core.clock import KST
from app.db.session import Database
from app.market import benchmarks as bm
from app.market.benchmarks import NY, daily_closes, history, record


def _rows(start: date, closes: list[float], tz: Any) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": datetime.combine(start + timedelta(days=i), time(0), tz).isoformat(),
            "closePrice": str(c),
        }
        for i, c in enumerate(closes)
    ][::-1]


class Fake:
    def __init__(self) -> None:
        self.asked: list[tuple[str, int]] = []
        self.kospi = [2500.0, 2450.0]

    async def index_candles(self, symbol: str, count: int = 60) -> list[dict[str, Any]]:
        self.asked.append((symbol, count))
        return _rows(date(2026, 11, 4), self.kospi if symbol == "KOSPI" else [1.0, 2.0], KST)

    async def candles(self, symbol: str, interval: str, count: int) -> tuple[list[dict[str, Any]], None]:
        self.asked.append((symbol, count))
        if symbol == "DIA":
            raise RuntimeError("일시 오류")  # 한 지표가 실패해도 나머지는 남긴다
        return _rows(date(2026, 11, 4), [600.0, 590.0], NY), None

    async def exchange_rate(self) -> dict[str, Any]:
        return {"rate": "1358.2"}


def test_daily_closes_use_market_date() -> None:
    rows = _rows(date(2026, 11, 4), [1.0, 2.0], NY)
    assert daily_closes(rows, NY) == {date(2026, 11, 4): 1.0, date(2026, 11, 5): 2.0}
    assert (
        daily_closes([{"timestamp": None, "closePrice": "1"}, {"timestamp": "x", "closePrice": "1"}], KST)
        == {}
    )


async def test_record_backfills_then_refreshes_and_fx_at_16() -> None:
    db = Database("sqlite://")
    db.create_all()
    fake = Fake()
    morning = datetime(2026, 11, 6, 7, 10, tzinfo=KST)
    out = await record(db, fake, morning)
    assert ("KOSPI", bm.BACKFILL_DAYS) in fake.asked  # 처음은 200일
    assert out["KOSPI"] == 2 and out["SPY"] == 2 and "DIA" not in out and "USDKRW" not in out  # 환율은 16시만
    h = history(db, date(2026, 11, 1))
    assert h["KOSPI"] == [
        {"day": "2026-11-04", "close": 2500.0, "source": "candle"},
        {"day": "2026-11-05", "close": 2450.0, "source": "candle"},
    ]
    assert [r["day"] for r in h["QQQ"]] == ["2026-11-04", "2026-11-05"]  # 뉴욕 날짜

    # 진행 중이던 봉이 마감값으로 바뀌면 덮어쓴다, 이번엔 최근 봉만
    fake.asked.clear()
    fake.kospi = [2500.0, 2440.0]
    out = await record(db, fake, datetime(2026, 11, 6, 16, 10, tzinfo=KST))
    assert ("KOSPI", bm.REFRESH_DAYS) in fake.asked
    assert out["KOSPI"] == 1 and out["USDKRW"] == 1
    h = history(db, date(2026, 11, 1))
    assert h["KOSPI"][-1]["close"] == 2440.0
    assert h["USDKRW"] == [{"day": "2026-11-06", "close": 1358.2, "source": "quote"}]


def test_benchmarks_endpoint(tmp_path: Any) -> None:
    from tests.test_api import _client

    with _client(tmp_path, auth_enabled=False) as c:
        r = c.get("/api/market/benchmarks?days=30")
        assert r.status_code == 200 and r.json()["items"] == {}
        assert c.get("/api/us/market/benchmarks").status_code == 404  # 공용(국장 DB) 주소
