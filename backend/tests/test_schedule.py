"""판단 일정 설정 (D-51): 자정 기준 격자 시각, 검증, 기본 일정 유지, 바꾼 시각 이전 슬롯 건너뛰기,
미장 서머타임/표준시 한국 시각 환산, 휴장일, 국장·미장 분리. 네트워크 없음 (가짜 토스)."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.clock import KST
from app.engine import decision_schedule as dsched
from app.engine.scheduler import daily_jobs
from tests.test_api import _client


def kst(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=KST)


def _grid(start: str, end: str, interval: int) -> list[str]:
    return [
        dsched.hhmm(m)
        for m in dsched.grid_minutes(dsched.to_minutes(start), dsched.to_minutes(end), interval)
    ]


def _stub(calls: list[str], name: str) -> Any:
    async def run() -> None:
        calls.append(name)

    return run


# ====================================================================== 격자·검증
def test_grid_is_anchored_at_midnight() -> None:
    assert _grid("08:10", "09:00", 15) == ["08:15", "08:30", "08:45", "09:00"]
    assert _grid("08:00", "17:00", 60) == [f"{h:02d}:00" for h in range(8, 18)]  # 끝 포함 10회
    assert _grid("08:00", "09:00", 15) == ["08:00", "08:15", "08:30", "08:45", "09:00"]
    assert _grid("08:05", "12:00", 120) == ["10:00", "12:00"]  # 08:05 가 아니라 자정 기준 2시간 단위
    assert _grid("09:35", "10:00", 20) == ["09:40", "10:00"]
    cfg = dsched.validate("08:00", "17:00", 60)
    assert [f"{t:%H:%M}" for t in cfg.times()][:2] == ["08:00", "09:00"] and len(cfg.times()) == 10
    assert cfg.as_dict() == {"start": "08:00", "end": "17:00", "interval_minutes": 60}
    assert dsched.interval_label(15) == "15분" and dsched.interval_label(60) == "1시간"
    assert dsched.interval_label(90) == "1시간 30분"


@pytest.mark.parametrize(
    ("start", "end", "interval", "msg"),
    [
        ("8시", "10:00", 15, "HH:MM"),
        ("24:00", "10:00", 15, "HH:MM"),
        ("08:03", "10:00", 15, "5분 단위"),
        ("08:00", "10:02", 15, "5분 단위"),
        ("08:00", "10:00", 7, "고를 수 없는 주기"),
        ("08:00", "10:00", 45, "고를 수 없는 주기"),
        ("10:00", "10:00", 15, "끝 시각은 시작 시각보다 늦어야"),
        ("12:00", "08:00", 60, "끝 시각은 시작 시각보다 늦어야"),
        ("00:00", "23:55", 5, "하루 288회로 너무 많아요 (최대 96회)"),
        ("08:10", "08:50", 60, "판단 시각이 하나도 없어요"),
    ],
)
def test_validation_errors(start: str, end: str, interval: int, msg: str) -> None:
    with pytest.raises(dsched.ScheduleError, match=msg.replace("(", r"\(").replace(")", r"\)")):
        dsched.validate(start, end, interval)


def test_full_day_every_15_minutes_is_the_maximum() -> None:
    assert len(dsched.validate("00:00", "23:55", 15).minutes()) == 96


def test_api_rejects_bad_schedule_in_korean(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 10), auth_enabled=False) as c:
        for body, msg in (
            ({"start": "08:03", "end": "17:00", "interval_minutes": 60}, "5분 단위"),
            ({"start": "08:00", "end": "17:00", "interval_minutes": 45}, "고를 수 없는 주기"),
            ({"start": "17:00", "end": "08:00", "interval_minutes": 60}, "끝 시각은"),
            ({"start": "00:00", "end": "23:55", "interval_minutes": 5}, "최대 96회"),
            ({"start": "08:10", "end": "08:50", "interval_minutes": 60}, "하나도 없어요"),
        ):
            r = c.put("/api/schedule", json=body)
            assert r.status_code == 400 and msg in r.json()["detail"], body
        assert c.get("/api/schedule").json()["custom"] is False  # 아무것도 저장되지 않음


# ====================================================================== 기본 일정 = 예전과 같음
def test_default_schedule_unchanged(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 7, 0), auth_enabled=False) as c:
        sc = c.get("/api/schedule").json()
        assert sc["market"] == "KR" and sc["timezone"] == "Asia/Seoul" and sc["tz_label"] == "한국 시각"
        assert sc["custom"] is False
        assert sc["times"] == ["08:35"] + [f"{h}:00" for h in range(10, 16)]
        assert sc["times_kst"] == sc["times"]
        assert (sc["start"], sc["end"], sc["interval_minutes"]) == ("08:35", "15:00", 60)
        assert sc["default"] == {"start": "08:35", "end": "15:00", "interval_minutes": 60}
        assert sc["intervals"] == [5, 10, 15, 20, 30, 60, 120, 180, 240]
        assert sc["market_hours"] == {"open": "09:00", "close": "15:30", "order_cutoff": "15:20"}
        assert sc["report_at"] == "15:45" and sc["outside_market"] == []
        assert sc["next_runs"][0] == {"name": "morning", "at": "2026-11-04T08:35:00+09:00"}
        kr = c.app.state.container  # type: ignore[attr-defined]
        # 디스패처 일정 = 예전 cron 일정 (08:35 · 10:00~15:00 · 15:45, 한국 시각 고정)
        want = [(datetime.combine(date(2026, 11, 4), t, KST), n) for t, n, _ in daily_jobs(kr.engine, 60)]
        assert kr.scheduler.jobs_for(date(2026, 11, 4)) == want
        assert kr.scheduler.sessions_per_day() == 7
        st = c.get("/api/agent/structure").json()["schedule"]
        assert st["morning"] == "08:35" and st["sessions"] == [f"{h}:00" for h in range(10, 16)]
        assert st["report"] == "15:45" and st["custom"] is False and st["interval_minutes"] == 60


def test_default_schedule_with_other_interval(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 7, 0), auth_enabled=False, agent_interval_minutes=180) as c:
        sc = c.get("/api/schedule").json()
        assert sc["times"] == ["08:35", "12:00", "15:00"]
        assert (sc["start"], sc["end"], sc["interval_minutes"]) == ("08:35", "15:00", 180)


async def test_default_dispatcher_fires_kr_jobs_once(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 8, 35, 1), auth_enabled=False) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        calls: list[str] = []
        kr.engine.morning = _stub(calls, "morning")
        kr.engine.hourly = _stub(calls, "hourly")
        assert await kr.scheduler.tick(wait=True) == ["2026-11-04 08:35 morning"]
        assert await kr.scheduler.tick(wait=True) == []  # 같은 작업은 한 번만
        kr.clock.set(kst(2026, 11, 4, 9, 30))
        assert await kr.scheduler.tick(wait=True) == []  # 기본 일정에 09:30 은 없다
        kr.clock.set(kst(2026, 11, 4, 10, 0, 1))
        assert await kr.scheduler.tick(wait=True) == ["2026-11-04 10:00 hourly"]
        assert calls == ["morning", "hourly"]


# ====================================================================== 화면에서 정한 일정
async def test_save_at_1533_does_not_fire_past_slot(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 15, 33), auth_enabled=False) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        calls: list[str] = []
        kr.engine.morning = _stub(calls, "morning")
        kr.engine.hourly = _stub(calls, "hourly")
        r = c.put("/api/schedule", json={"start": "08:00", "end": "17:00", "interval_minutes": 30})
        assert r.status_code == 200
        sc = r.json()
        assert sc["custom"] is True and len(sc["times"]) == 19 and "15:33" not in sc["times"]
        assert (sc["start"], sc["end"], sc["interval_minutes"]) == ("08:00", "17:00", 30)
        decisions = [x for x in sc["next_runs"] if x["name"] != "report"]
        assert decisions[0] == {"name": "hourly", "at": "2026-11-04T16:00:00+09:00"}  # 다음 격자 슬롯
        # 15:30 슬롯(유예 10분 안)을 늦게 돌리지 않는다
        assert "2026-11-04 15:30 hourly" in kr.kv.get("sched_fired")["2026-11-04"]
        assert await kr.scheduler.tick(wait=True) == []
        kr.clock.set(kst(2026, 11, 4, 15, 40))
        assert await kr.scheduler.tick(wait=True) == []
        kr.clock.set(kst(2026, 11, 4, 16, 0, 1))
        assert await kr.scheduler.tick(wait=True) == ["2026-11-04 16:00 hourly"]
        assert calls == ["hourly"]
        chat = c.get("/api/chat").json()
        assert (
            chat[-1]["content"] == "⏰ 판단 일정을 바꿨어요: 08:00~17:00, 30분마다 (19회) — 다음 판단 16:00"
        )
        ev = c.get("/api/events?type=CONFIG").json()
        assert ev[0]["payload"]["schedule"] == {"start": "08:00", "end": "17:00", "interval_minutes": 30}


async def test_custom_schedule_runs_slots_outside_market_and_report(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 8, 20), auth_enabled=False) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        calls: list[str] = []
        kr.engine.morning = _stub(calls, "morning")
        kr.engine.hourly = _stub(calls, "hourly")
        sc = c.put("/api/schedule", json={"start": "08:00", "end": "17:00", "interval_minutes": 60}).json()
        assert sc["times"] == [f"{h:02d}:00" for h in range(8, 18)]
        assert sc["outside_market"] == [
            "08:00",
            "16:00",
            "17:00",
        ]  # 08:30 전 · 15:20 뒤 (판단은 하고 주문은 다음 장)
        assert sc["report_at"] == "15:45" and sc["default"]["start"] == "08:35"
        chat = c.get("/api/chat").json()
        assert (
            chat[-1]["content"] == "⏰ 판단 일정을 바꿨어요: 08:00~17:00, 1시간마다 (10회) — 다음 판단 09:00"
        )
        st = c.get("/api/agent/structure").json()["schedule"]
        assert st["custom"] is True and st["morning"] == "08:00" and st["sessions"][0] == "09:00"
        assert st["sessions"][-1] == "17:00" and st["interval_minutes"] == 60
        assert c.get("/api/system").json()["agent_interval_minutes"] == 60
        assert kr.scheduler.sessions_per_day() == 10
        jobs = kr.scheduler.jobs_for(date(2026, 11, 4))
        assert [n for _, n in jobs][:2] == ["morning", "hourly"]  # 장 시작 전 슬롯 = 장전 판단
        assert [(f"{t:%H:%M}", n) for t, n in jobs if f"{t:%H:%M}" == "15:45"] == [("15:45", "report")]
        kr.clock.set(kst(2026, 11, 4, 17, 0, 1))
        assert await kr.scheduler.tick(wait=True) == ["2026-11-04 17:00 hourly"]  # 장 시간 밖 슬롯도 판단
        assert calls == ["hourly"]


async def test_report_waits_for_running_session(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 10), auth_enabled=False) as c:
        sch = c.app.state.container.scheduler  # type: ignore[attr-defined]
        done: list[str] = []
        await sch._busy.acquire()
        task = asyncio.ensure_future(sch._guarded("report", _stub(done, "report"), busy_wait=True)())
        await sch._guarded("hourly", _stub(done, "hourly"))()  # 판단은 다른 작업 중이면 건너뜀
        await asyncio.sleep(0.01)
        assert done == []
        sch._busy.release()
        await task
        assert done == ["report"]  # 리포트는 기다렸다가 만든다


async def test_non_trading_day_has_no_runs(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 7, 10, 0, 1), auth_enabled=False) as c:  # 토요일
        kr = c.app.state.container  # type: ignore[attr-defined]
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        c.put("/api/schedule", json={"start": "08:00", "end": "17:00", "interval_minutes": 60})
        c.put("/api/us/schedule", json={"start": "08:00", "end": "17:00", "interval_minutes": 60})
        assert kr.scheduler.jobs_for(date(2026, 11, 7)) == []
        assert us.scheduler.jobs_for(date(2026, 11, 7)) == []
        assert us.scheduler.jobs_for(date(2026, 11, 26)) == []  # 추수감사절 (NYSE 휴장)
        assert kr.scheduler.jobs_for(date(2026, 12, 25)) == []  # 성탄절 (KRX 휴장)
        assert await kr.scheduler.tick(wait=True) == []
        assert kr.scheduler.next_runs(1)[0] == {"name": "morning", "at": "2026-11-09T08:00:00+09:00"}
        assert c.get("/api/schedule").json()["day"] == "2026-11-09"


# ====================================================================== 미장: 뉴욕 시각 · 서머타임
async def test_us_custom_schedule_kst_across_dst(tmp_path: Path) -> None:
    # 2026-10-29 12:00 KST = 뉴욕 10/28 23:00 → 다음 장은 10/29 (서머타임 EDT, UTC-4)
    with _client(tmp_path, kst(2026, 10, 29, 12), auth_enabled=False) as c:
        us = c.app.state.containers["US"]  # type: ignore[attr-defined]
        sc = c.put("/api/us/schedule", json={"start": "09:00", "end": "16:00", "interval_minutes": 60}).json()
        assert (
            sc["market"] == "US"
            and sc["timezone"] == "America/New_York"
            and sc["tz_label"] == "미국 동부 시각"
        )
        assert sc["day"] == "2026-10-29"
        assert sc["times"] == [f"{h:02d}:00" for h in range(9, 17)]
        assert sc["times_kst"] == ["22:00", "23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00"]
        assert sc["market_hours"] == {"open": "09:30", "close": "16:00", "order_cutoff": "15:50"}
        assert sc["outside_market"] == ["16:00"] and sc["report_at"] == "16:15"
        assert sc["next_runs"][0] == {
            "name": "morning",
            "at": "2026-10-29T22:00:00+09:00",
        }  # 09:00 < 09:30 장전
        chat = c.get("/api/us/chat").json()[-1]["content"]
        assert chat == (
            "⏰ 판단 일정을 바꿨어요: 09:00~16:00 (미국 동부 시각), 1시간마다 (8회) "
            "— 다음 판단 10/29 09:00 (한국 10/29 22:00)"
        )
        # 표준시(EST, UTC-5) 날짜: 같은 뉴욕 시각이 한국 시각으로 1시간 늦다
        us.clock.set(kst(2026, 11, 4, 12))
        sc = c.get("/api/us/schedule").json()
        assert sc["day"] == "2026-11-04" and sc["times"][0] == "09:00"
        assert sc["times_kst"] == ["23:00", "00:00", "01:00", "02:00", "03:00", "04:00", "05:00", "06:00"]
        calls: list[str] = []
        us.engine.morning = _stub(calls, "morning")
        us.engine.hourly = _stub(calls, "hourly")
        us.clock.set(kst(2026, 11, 4, 23, 0, 1))
        assert await us.scheduler.tick(wait=True) == ["2026-11-04 23:00 morning"]
        us.clock.set(kst(2026, 11, 5, 0, 0, 1))
        assert await us.scheduler.tick(wait=True) == ["2026-11-04 00:00 hourly"]
        assert calls == ["morning", "hourly"]


def test_us_default_schedule_unchanged(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 12), auth_enabled=False) as c:
        sc = c.get("/api/us/schedule").json()
        assert sc["custom"] is False and sc["day"] == "2026-11-04"
        assert sc["times"] == ["09:05"] + [f"{h}:30" for h in range(10, 16)]
        assert sc["times_kst"] == ["23:05", "00:30", "01:30", "02:30", "03:30", "04:30", "05:30"]
        assert (sc["start"], sc["end"], sc["interval_minutes"]) == ("09:05", "15:30", 60)
        assert sc["report_at"] == "16:15" and sc["outside_market"] == []


def test_us_schedule_independent_from_kr(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 4, 8, 20), auth_enabled=False) as c:
        assert (
            c.put(
                "/api/schedule", json={"start": "08:00", "end": "17:00", "interval_minutes": 60}
            ).status_code
            == 200
        )
        assert c.get("/api/us/schedule").json()["custom"] is False
        us = c.put("/api/us/schedule", json={"start": "09:00", "end": "15:00", "interval_minutes": 30}).json()
        assert us["custom"] is True and us["interval_minutes"] == 30
        kr = c.get("/api/schedule").json()
        assert kr["custom"] is True and kr["interval_minutes"] == 60 and kr["start"] == "08:00"
        kr_chat = [m["content"] for m in c.get("/api/chat").json()]
        us_chat = [m["content"] for m in c.get("/api/us/chat").json()]
        assert sum("판단 일정을 바꿨어요" in m for m in kr_chat) == 1
        assert sum("판단 일정을 바꿨어요" in m for m in us_chat) == 1
        reset = c.delete("/api/us/schedule").json()
        assert reset["custom"] is False and reset["start"] == "09:05"
        assert "기본값으로 되돌렸어요" in c.get("/api/us/chat").json()[-1]["content"]
        assert c.get("/api/schedule").json()["custom"] is True  # 국장은 그대로
        assert c.delete("/api/schedule").json()["custom"] is False
        assert c.get("/api/agent/structure").json()["schedule"]["morning"] == "08:35"
