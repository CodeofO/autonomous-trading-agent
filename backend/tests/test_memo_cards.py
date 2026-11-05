"""메모 카드 (D-52): 만들기·검증·중복·만료·관심종목 연동·기억 설정·프롬프트 반영, 국장·미장 분리, 종목 검색."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.core.clock import KST
from tests.test_api import _client


def kst(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=KST)


NOW = kst(2026, 11, 4, 10)  # 수요일


def _watchlist(c: object, prefix: str = "/api") -> list[str]:
    return [w["symbol"] for w in c.get(f"{prefix}/watchlist").json()]  # type: ignore[attr-defined]


def test_create_validate_and_list_cards(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        empty = c.get("/api/memory/cards").json()
        assert empty == {
            "cards": [],
            "settings": {"use_recent_messages": True, "recent_messages": 5},
            "today": "2026-11-04",
        }
        w = c.post("/api/memory/cards", json={"kind": "watch", "symbol": "005930", "expires_on": None})
        assert w.status_code == 200
        card = w.json()
        assert {
            k: card[k] for k in ("kind", "symbol", "name", "text", "active", "expires_on", "expired")
        } == {
            "kind": "watch",
            "symbol": "005930",
            "name": "삼성전자",
            "text": "",
            "active": True,
            "expires_on": None,
            "expired": False,
        }
        assert card["created_at"].startswith("2026-11-04T01:00") and card["updated_at"] == card["created_at"]
        assert "005930" in _watchlist(c)  # 관심 종목 카드 → 관심종목 목록에도
        avoid = c.post(
            "/api/memory/cards", json={"kind": "avoid", "symbol": "247540", "text": "변동성 너무 큼"}
        ).json()
        assert avoid["name"] == "에코프로비엠" and avoid["text"] == "변동성 너무 큼"
        assert "247540" not in _watchlist(c)  # 피할 종목은 관심종목에 넣지 않음
        note = c.post(
            "/api/memory/cards",
            json={"kind": "note", "text": "  현금 30% 유지  ", "expires_on": "2026-11-11"},
        ).json()
        assert (
            note["symbol"] is None and note["text"] == "현금 30% 유지" and note["expires_on"] == "2026-11-11"
        )
        c.post("/api/memory/cards", json={"kind": "watch", "symbol": "000660", "text": "HBM"})

        errors = [
            ({"kind": "bogus", "symbol": "005930"}, "카드 종류"),
            ({"kind": "focus"}, "종목을 골라"),
            ({"kind": "focus", "symbol": "999999"}, "찾을 수 없는 종목"),
            ({"kind": "focus", "symbol": "삼성 전자"}, "종목코드 형식"),
            ({"kind": "note", "text": "   "}, "운용 지시 내용"),
            ({"kind": "note", "text": "가" * 501}, "500자"),
            ({"kind": "watch", "symbol": "005930"}, "이미 켜져 있는 같은 카드"),
            ({"kind": "note", "text": "x", "expires_on": "2026-11-03"}, "만료일은 오늘"),
        ]
        for body, msg in errors:
            r = c.post("/api/memory/cards", json=body)
            assert r.status_code == 400 and msg in r.json()["detail"], (body, r.json())

        cards = c.get("/api/memory/cards").json()["cards"]
        # 종류 순(관심 → 주목 → 피할 → 운용 지시), 같은 종류는 새 카드 먼저
        assert [(x["kind"], x["symbol"]) for x in cards] == [
            ("watch", "000660"),
            ("watch", "005930"),
            ("avoid", "247540"),
            ("note", None),
        ]
        ev = [e["message"] for e in c.get("/api/events?type=CONFIG").json()]
        assert "메모 카드 추가: 관심 종목 삼성전자(005930)" in ev


def test_patch_delete_duplicates_and_expiry(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        a = c.post("/api/memory/cards", json={"kind": "watch", "symbol": "005930"}).json()
        # 끄면 같은 카드를 새로 만들 수 있고, 둘 다 켜려고 하면 막는다
        off = c.patch(f"/api/memory/cards/{a['id']}", json={"active": False}).json()
        assert off["active"] is False
        b = c.post("/api/memory/cards", json={"kind": "watch", "symbol": "005930"}).json()
        r = c.patch(f"/api/memory/cards/{a['id']}", json={"active": True})
        assert r.status_code == 400 and "이미 켜져 있는" in r.json()["detail"]
        # 종류 바꾸기 (종목 카드끼리만), 메모·만료일 고치기
        moved = c.patch(
            f"/api/memory/cards/{b['id']}", json={"kind": "focus", "text": "실적 발표 전까지"}
        ).json()
        assert (
            moved["kind"] == "focus" and moved["text"] == "실적 발표 전까지" and moved["symbol"] == "005930"
        )
        r = c.patch(f"/api/memory/cards/{b['id']}", json={"kind": "note"})
        assert r.status_code == 400 and "서로 바꿀 수 없어요" in r.json()["detail"]
        r = c.patch(f"/api/memory/cards/{b['id']}", json={"kind": "nope"})
        assert r.status_code == 400
        r = c.patch(f"/api/memory/cards/{b['id']}", json={"expires_on": "2026-11-01"})
        assert r.status_code == 400 and "만료일" in r.json()["detail"]
        today = c.patch(f"/api/memory/cards/{b['id']}", json={"expires_on": "2026-11-04"}).json()
        assert today["expires_on"] == "2026-11-04" and today["expired"] is False  # 그날까지는 유효
        assert c.patch("/api/memory/cards/9999", json={"active": False}).status_code == 404
        n = c.post("/api/memory/cards", json={"kind": "note", "text": "현금 비중 유지"}).json()
        r = c.patch(f"/api/memory/cards/{n['id']}", json={"text": " "})
        assert r.status_code == 400 and "비울 수 없어요" in r.json()["detail"]

        st = c.get("/api/agent/structure").json()["memory"]
        assert st["cards"] == [
            {"kind": "focus", "symbol": "005930", "name": "삼성전자", "text": "실적 발표 전까지"},
            {"kind": "note", "symbol": None, "name": None, "text": "현금 비중 유지"},
        ]  # 꺼진 카드(a)는 빠진다

        # 다음 날: 만료일이 지난 카드는 기억에서 빠지고 expired=true
        c.app.state.container.clock.set(kst(2026, 11, 5, 10))  # type: ignore[attr-defined]
        got = {x["id"]: x for x in c.get("/api/memory/cards").json()["cards"]}
        assert got[b["id"]]["expired"] is True and got[n["id"]]["expired"] is False
        assert c.get("/api/memory/cards").json()["today"] == "2026-11-05"
        assert [x["kind"] for x in c.get("/api/agent/structure").json()["memory"]["cards"]] == ["note"]
        # 만료된 카드와 같은 종목으로 새 카드를 켤 수 있다
        assert c.post("/api/memory/cards", json={"kind": "focus", "symbol": "005930"}).status_code == 200

        # 삭제: 204, 관심종목 목록 항목은 남긴다
        assert c.delete(f"/api/memory/cards/{a['id']}").status_code == 204
        assert c.delete(f"/api/memory/cards/{a['id']}").status_code == 404
        assert a["id"] not in {x["id"] for x in c.get("/api/memory/cards").json()["cards"]}
        assert "005930" in _watchlist(c)
        ev = [e["message"] for e in c.get("/api/events?type=CONFIG").json()]
        assert any(m.startswith("메모 카드 삭제: 관심 종목 삼성전자") for m in ev)
        assert any(m.startswith("메모 카드 변경: 주목할 종목 삼성전자") for m in ev)


def test_prompt_memory_and_recent_messages_setting(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        runner = c.app.state.container.runner  # type: ignore[attr-defined]
        c.post("/api/chat", json={"message": "카카오 계속 지켜봐 줘"})
        c.post("/api/memory/cards", json={"kind": "watch", "symbol": "000660", "text": "HBM 수요"})
        c.post("/api/memory/cards", json={"kind": "focus", "symbol": "005380"})
        c.post("/api/memory/cards", json={"kind": "avoid", "symbol": "247540"})
        c.post("/api/memory/cards", json={"kind": "note", "text": "현금 30% 이상 유지"})
        mem = runner._memory()
        for part in (
            "관심 종목",
            "- SK하이닉스(000660): HBM 수요",
            "주목할 종목",
            "- 현대차(005380)",
            "피할 종목(매수하지 말 것",
            "- 에코프로비엠(247540)",
            "운용 지시:\n- 현금 30% 이상 유지",
            "최근 대화 메모",
            "카카오 계속 지켜봐 줘",
        ):
            assert part in mem, part
        # 순서: 카드 → 최근 판단 기록 → 최근 대화 메모
        assert mem.index("관심 종목") < mem.index("피할 종목") < mem.index("운용 지시")
        assert mem.index("운용 지시") < mem.index("최근 판단 기록") < mem.index("최근 대화 메모")
        assert "BUY" in mem[mem.index("피할 종목") :].split("\n")[0]  # 매수 금지를 프롬프트로 분명히
        assert "피할 종목" in runner._system()  # 시스템 프롬프트에도 규칙

        r = c.put("/api/memory/settings", json={"use_recent_messages": False})
        assert r.json() == {"use_recent_messages": False, "recent_messages": 5}
        assert c.get("/api/memory/cards").json()["settings"]["use_recent_messages"] is False
        mem = runner._memory()
        assert "최근 대화 메모" not in mem and "카카오" not in mem and "SK하이닉스" in mem
        st = c.get("/api/agent/structure").json()["memory"]
        assert st["memos"] == [] and st["use_recent_messages"] is False
        rule = next(x for x in st["rules"] if x["name"] == "투자자 메모")
        assert "끌 수 있다" in rule["detail"] and "지금 꺼짐" in rule["detail"]
        assert st["rules"][0]["name"] == "메모 카드"
        assert any("최근 대화 메모 끔" in e["message"] for e in c.get("/api/events?type=CONFIG").json())
        c.put("/api/memory/settings", json={"use_recent_messages": True})
        assert "카카오" in runner._memory()


def test_memo_cards_are_per_market(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        nv = c.post("/api/us/memory/cards", json={"kind": "watch", "symbol": "nvda", "expires_on": None})
        assert nv.status_code == 200 and nv.json()["symbol"] == "NVDA" and nv.json()["name"] == "엔비디아"
        assert "NVDA" in _watchlist(c, "/api/us") and "NVDA" not in _watchlist(c)
        r = c.post("/api/us/memory/cards", json={"kind": "focus", "symbol": "005930"})
        assert (
            r.status_code == 400 and "미장 종목 목록" in r.json()["detail"]
        )  # 국장 종목은 미장 카드로 못 만든다
        r = c.post("/api/memory/cards", json={"kind": "focus", "symbol": "AAPL"})
        assert r.status_code == 400 and "국장 종목 목록" in r.json()["detail"]
        c.post("/api/memory/cards", json={"kind": "note", "text": "국장 지시"})
        us = c.get("/api/us/memory/cards").json()
        kr = c.get("/api/memory/cards").json()
        assert [x["symbol"] for x in us["cards"]] == ["NVDA"] and [x["kind"] for x in kr["cards"]] == ["note"]
        assert us["today"] == "2026-11-03"  # 미장 만료 기준 = 뉴욕 날짜 (한국 11/04 10:00 = 뉴욕 11/03 21:00)
        # 설정도 스택마다
        c.put("/api/us/memory/settings", json={"use_recent_messages": False})
        assert c.get("/api/memory/cards").json()["settings"]["use_recent_messages"] is True
        us_runner = c.app.state.containers["US"].runner  # type: ignore[attr-defined]
        assert "엔비디아(NVDA)" in us_runner._memory()
        assert "국장 지시" not in us_runner._memory()


def test_market_search(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        assert c.get("/api/market/search?q=").json() == []
        assert c.get("/api/market/search?q=%20").json() == []
        assert c.get("/api/market/search?q=삼성").json() == [
            {"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}
        ]
        assert c.get("/api/market/search?q=0059").json()[0]["symbol"] == "005930"  # 코드 앞자리
        assert c.get("/api/market/search?q=naver").json()[0]["symbol"] == "035420"  # 대소문자 무시
        assert len(c.get("/api/market/search?q=0&limit=1").json()) == 1
        assert c.get("/api/market/search?q=0&limit=100").status_code == 200  # 최대 50으로 줄인다
        assert c.get("/api/market/search?q=엔비디아").json() == []  # 국장 목록에는 미국 종목 없음
        us = c.get("/api/us/market/search?q=nv").json()
        assert us[0] == {"symbol": "NVDA", "name": "엔비디아", "market": "NASDAQ"}  # 티커 앞자리
        assert c.get("/api/us/market/search?q=apple").json()[0]["symbol"] == "AAPL"  # 영문 이름
        assert c.get("/api/us/market/search?q=코카").json()[0]["symbol"] == "KO"  # 한글 이름
        assert c.get("/api/us/market/search?q=s&limit=3").json()[0]["symbol"] == "SPY"  # 티커 앞자리가 먼저
        assert c.get("/api/us/market/search?q=삼성").json() == []


def test_migration_0003_creates_memory_cards_on_both_dbs(tmp_path: Path) -> None:
    """파일 DB 면 국장·미장 DB 모두 Alembic 0003 으로 memory_cards 가 생긴다."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, inspect, text

    from app.core.clock import SimulatedClock
    from app.core.config import Settings
    from app.main import create_app
    from tests.fake_toss import FakeTossClient

    clock = SimulatedClock(NOW)
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'kr.db'}",
        data_dir=tmp_path,
        enable_scheduler=False,
    )
    with TestClient(create_app(s, toss_client=FakeTossClient(clock), clock=clock)) as c:
        assert c.post("/api/memory/cards", json={"kind": "note", "text": "국장"}).status_code == 200
        assert c.post("/api/us/memory/cards", json={"kind": "watch", "symbol": "AAPL"}).status_code == 200
    for name in ("kr.db", "us_market.db"):
        eng = create_engine(f"sqlite:///{tmp_path / name}")
        assert "memory_cards" in inspect(eng).get_table_names()
        with eng.connect() as con:
            assert con.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
            assert con.execute(text("SELECT count(*) FROM memory_cards")).scalar() == 1
        eng.dispose()
