"""홈 '지수 대비 성과': 계좌 하루 마감 자산과 지수 종가를 같은 날짜 축에."""

from __future__ import annotations

from datetime import date, datetime

from app.core.clock import KST
from app.db.models import BenchmarkDaily, EquitySnapshot
from app.db.session import Database
from app.engine.performance import performance


def test_daily_performance_aligns_account_and_benchmarks() -> None:
    db = Database("sqlite://")
    db.create_all()
    with db.session() as s:
        for d, h, eq in [(2, 10, 100.0), (2, 15, 102.0), (3, 15, 99.0), (5, 15, 105.0)]:
            s.add(EquitySnapshot(ts=datetime(2026, 11, d, h, tzinfo=KST), equity=eq, cash=0))
        for d, close in [(30, 990.0)]:
            s.add(BenchmarkDaily(key="KOSPI", day=date(2026, 10, d), close=close, source="candle"))
        for d, close in [(3, 1010.0), (4, 1020.0), (5, 1040.0)]:
            s.add(BenchmarkDaily(key="KOSPI", day=date(2026, 11, d), close=close, source="candle"))
        s.add(BenchmarkDaily(key="SPY", day=date(2026, 11, 3), close=500.0, source="candle"))
    now = datetime(2026, 11, 5, 18, tzinfo=KST)
    out = performance(db, db, "KR", now, lambda t: t.astimezone(KST).date(), 30)
    assert out["days"] == ["2026-11-02", "2026-11-03", "2026-11-05"]
    assert out["account"] == [102.0, 99.0, 105.0]  # 날짜별 마지막 값
    assert out["account_pct"] == [0.0, -2.941, 2.941]
    kospi = next(b for b in out["benchmarks"] if b["key"] == "KOSPI")
    # 11/2 종가가 없으면 직전(10/30) 종가를 이어 쓰고, 그 값이 기준(0%)
    assert kospi["name"] == "코스피" and kospi["pct"] == [0.0, 2.02, 5.051]
    assert [b["key"] for b in out["benchmarks"]] == ["KOSPI"]  # 기록 없는 코스닥·미장 지수는 뺀다
    assert performance(db, db, "KR", datetime(2026, 1, 1, tzinfo=KST), lambda t: t.date(), 2)["days"]
