"""대시보드 주요 지표: 지수·미국 ETF·환율·국채 묶음, 전일 대비, 일부 실패 허용, 1분 캐시 (실제 호출 없음)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.core.clock import KST, SimulatedClock
from app.market.indicators import MarketIndicators
from app.market.toss import TossApiError
from tests.test_api import _client

NOW = datetime(2026, 11, 4, 8, 45, tzinfo=KST)


def _candles(closes: list[float], hour: int = 0) -> list[dict[str, Any]]:
    """오래된 순 종가 → 토스 형식(최신순) 일봉."""
    d0 = date(2026, 11, 3) - timedelta(days=len(closes) - 1)
    rows = [
        {
            "timestamp": datetime.combine(d0 + timedelta(days=i), time(hour), KST).isoformat(),
            "closePrice": str(c),
        }
        for i, c in enumerate(closes)
    ]
    return rows[::-1]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def _hit(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise TossApiError(500, "internal", name)

    async def index_prices(self, symbols: tuple[str, ...]) -> list[dict[str, Any]]:
        self._hit("index_prices")
        px = {"KOSPI": "5648.79", "KOSDAQ": "704.39", "KR_BOND_10Y": "4.002"}
        return [{"symbol": s, "timestamp": None, "lastPrice": px[s]} for s in symbols]

    async def index_candles(self, symbol: str, count: int = 60) -> list[dict[str, Any]]:
        self._hit(f"index_candles:{symbol}")
        return {
            "KOSPI": _candles([5800.0, 5859.08, 5648.79]),
            "KOSDAQ": _candles([700.0, 704.39]),
            "KR_BOND_10Y": _candles([4.01, 4.014, 4.002]),
        }[symbol]

    async def prices(self, symbols: list[str]) -> dict[str, tuple[float, datetime | None]]:
        self._hit("prices")
        return {"QQQ": (686.13, NOW), "SPY": (754.96, NOW)}

    async def candles(
        self, symbol: str, interval: str = "1d", count: int = 200
    ) -> tuple[list[dict[str, Any]], None]:
        self._hit(f"candles:{symbol}")
        return {"QQQ": _candles([681.09, 685.42], 14), "SPY": _candles([750.0, 755.0], 14)}[symbol], None

    async def exchange_rate(self) -> dict[str, Any]:
        self._hit("fx")
        return {"rate": "1344.5", "rateChangeType": "UP"}


async def test_snapshot_combines_indices_etfs_fx_and_bonds() -> None:
    fake = FakeClient()
    snap = await MarketIndicators(fake, SimulatedClock(NOW)).snapshot()  # type: ignore[arg-type]
    by = {r["key"]: r for r in snap["items"]}
    assert list(by) == ["KOSPI", "KOSDAQ", "QQQ", "SPY", "USDKRW", "KR_BOND_10Y"]

    k = by["KOSPI"]
    assert (k["price"], k["prev_close"], k["change_pct"]) == (5648.79, 5859.08, -3.59)
    assert k["spark"] == [5800.0, 5859.08, 5648.79] and k["error"] is False

    q = by["QQQ"]  # 나스닥 지수는 토스에 없어 QQQ ETF 로 대신하고 그렇게 표시
    assert q["name"] == "나스닥100" and q["note"] == "QQQ ETF" and q["unit"] == "$"
    assert q["prev_close"] == 681.09 and q["change_pct"] == 0.74
    assert q["spark"][-1] == 686.13  # 시간외 현재가가 마지막 점

    b = by["KR_BOND_10Y"]
    assert b["change"] == -0.012 and b["change_pct"] is None  # 금리는 %p 로만
    fx = by["USDKRW"]
    assert fx["price"] == 1344.5 and fx["direction"] == "up" and fx["change"] is None


async def test_one_failure_keeps_the_rest_and_cache_limits_calls() -> None:
    fake = FakeClient()
    fake.fail = {"prices", "index_candles:KOSDAQ"}
    ind = MarketIndicators(fake, SimulatedClock(NOW))  # type: ignore[arg-type]
    by = {r["key"]: r for r in (await ind.snapshot())["items"]}
    assert by["QQQ"]["error"] is True and by["QQQ"]["price"] is None
    assert by["KOSDAQ"]["price"] == 704.39 and by["KOSDAQ"]["change"] is None  # 일봉 실패 → 대비만 빈칸
    assert by["KOSPI"]["error"] is False

    n = len(fake.calls)
    await ind.snapshot()
    assert len(fake.calls) == n  # 15초 안에는 다시 부르지 않는다
    # 현재가 캐시가 지나면 현재가는 다시 받고, 실패했던 일봉(KOSDAQ)만 다시 시도한다
    ind.ttl = 0
    await ind.snapshot()
    again = fake.calls[n:]
    assert "index_candles:KOSPI" not in again and "index_candles:KOSDAQ" in again
    fake.fail = set()
    await ind.snapshot()
    m = len(fake.calls)
    await ind.snapshot()  # 일봉이 다 있으면 현재가 3번만
    assert len(fake.calls) - m == 3
    ind.candle_ttl = 0
    await ind.snapshot()  # 일봉 캐시가 지나면 일봉도 다시
    assert any(c.startswith("index_candles") for c in fake.calls[m + 3 :])


def test_indicators_endpoint(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False) as client:
        r = client.get("/api/market/indicators")
        assert r.status_code == 200
        items = r.json()["items"]
        assert [i["key"] for i in items][:2] == ["KOSPI", "KOSDAQ"] and len(items) == 6
        assert items[0]["price"] == 2812.45  # 가짜 토스의 코스피
