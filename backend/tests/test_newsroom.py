"""뉴스 룸 (D-56): 섹션별 기사 고르기, 07·12·17시 분배기, 요약(선택), API, 마이그레이션.

네이버·LLM 은 respx 가짜 응답만 쓴다 (실제 외부 호출 없음, 유료 API 없음).
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.agent.llm import LLMClient, llm_routes
from app.core.clock import KST, SimulatedClock
from app.core.http import SafeHttpClient
from app.db.models import LlmUsage, NewsEdition
from app.db.session import Database
from app.engine.settings_store import SettingsStore
from app.research import naver, newsroom
from app.research.naver import NaverNewsClient
from app.research.newsroom import SECTIONS, NewsRoom, next_slot, pick_stories
from tests.test_api import _client

KEYS = {"naver_client_id": "id-test-0001", "naver_client_secret": "secret-test"}
LLM_BASE = "https://llm.example.test/v1"
NOW = datetime(2026, 11, 5, 7, 0, 1, tzinfo=KST)  # 목요일


def kst(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=KST)


def pub(t: datetime) -> str:
    """네이버 pubDate 형식 (RFC 822)."""
    return t.astimezone(KST).strftime("%a, %d %b %Y %H:%M:%S +0900")


def art(title: str, link: str, at: datetime, desc: str = "") -> dict[str, Any]:
    return {"title": title, "description": desc, "link": link, "pubDate": pub(at)}


def naver_item(title: str, link: str, at: datetime, desc: str = "") -> dict[str, str]:
    return {"title": title, "originallink": link, "link": link, "description": desc, "pubDate": pub(at)}


# 검색어 → 기사 (나머지 검색어는 빈 목록)
def feed(now: datetime) -> dict[str, list[dict[str, str]]]:
    h = timedelta(hours=1)
    return {
        "국제 정세": [
            naver_item(
                "<b>미중</b> 정상회담 개최…관세 협상 진전", "https://a.example.com/1", now - h, "양국 정상"
            ),
            naver_item("미중 정상회담 개최 관세 협상 진전 기대", "https://b.example.com/2", now - 2 * h),
            naver_item("유럽 의회 선거 결과 발표", "https://c.example.com/3", now - 3 * h),
            naver_item("사흘 전 기사", "https://c.example.com/old", now - 72 * h),
        ],
        "뉴욕증시": [naver_item("뉴욕증시 기술주 강세 마감", "https://d.example.com/4", now - h)],
        "코스피": [
            naver_item("코스피 외국인 매수에 상승 출발", "https://e.example.com/5", now - h),
            # 앞 섹션(세계·국제)에 나온 사건 → 국내 섹션에서는 뺀다
            naver_item("[속보] 미중 정상회담 개최 관세 협상 진전", "https://f.example.com/6", now - h),
        ],
        "속보": [naver_item("[속보] 국회 본회의 예산안 처리", "https://g.example.com/7", now - h)],
    }


def naver_handler(now: datetime, fail: set[str] | None = None) -> Any:
    items = feed(now)

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["query"]
        if fail is not None and (not fail or q in fail):
            return httpx.Response(400, json={"errorCode": "SE99"})
        return httpx.Response(200, json={"items": items.get(q, [])})

    return handler


def fast_naver() -> NaverNewsClient:
    return NaverNewsClient(
        "id", "secret", http=SafeHttpClient(naver.ROUTES, rate_per_sec=1000, backoff_scale=0.0)
    )


def fast_llm() -> LLMClient:
    return LLMClient(
        LLM_BASE,
        "news-model",
        "k",
        api="chat",
        http=SafeHttpClient(llm_routes(LLM_BASE), rate_per_sec=1000, backoff_scale=0.0, max_retries=0),
    )


def llm_reply(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1500, "completion_tokens": 200, "total_tokens": 1700},
        },
    )


def room(clock: SimulatedClock, *, llm: LLMClient | None = None) -> tuple[NewsRoom, Database]:
    db = Database("sqlite://")
    db.create_all()
    return NewsRoom(db, clock, SettingsStore(db), news=fast_naver(), llm=lambda: llm), db


# ====================================================================== 기사 고르기
def test_same_story_is_grouped_and_ranked_by_coverage() -> None:
    now = NOW
    h = timedelta(hours=1)
    items = [
        art("유럽 의회 선거 결과 발표", "https://c.example.com/3", now - 10 * 60 * timedelta(seconds=1)),
        art("미중 정상회담 개최…관세 협상 진전", "https://a.example.com/1", now - 2 * h),
        art("[단독] 미중 정상회담 개최 관세 협상 진전 기대", "https://b.example.com/2", now - h),
        art("미중 정상회담 개최…관세 협상 진전", "https://a.example.com/1", now - 2 * h),  # 같은 링크
        art("사흘 전 기사", "https://c.example.com/old", now - 72 * h),
        art("", "https://x.example.com/empty", now),
    ]
    out = pick_stories(items, now)
    assert [o["title"] for o in out] == [
        "[단독] 미중 정상회담 개최 관세 협상 진전 기대",  # 두 매체 → 위로, 대표는 가장 최근 기사
        "유럽 의회 선거 결과 발표",
    ]
    assert out[0]["related"] == 2 and out[0]["domain"] == "b.example.com"
    assert out[0]["published_at"] == (now - h).isoformat()
    assert out[1]["related"] == 1


TOPICS = ("반도체 수출", "부동산 대책", "원전 정책", "의료 개혁", "철도 파업", "태풍 북상", "물가 지표")


def test_story_from_earlier_section_is_skipped_and_limit() -> None:
    taken: list[frozenset[str]] = []
    first = pick_stories([art("미중 정상회담 개최 관세 협상", "https://a/1", NOW)], NOW, taken=taken)
    assert len(first) == 1 and len(taken) == 1
    second = pick_stories(
        [art("[속보] 미중 정상회담 개최 관세 협상", "https://b/2", NOW)]
        + [art(f"{w} 관련 발표", f"https://c/{w}", NOW) for w in TOPICS],
        NOW,
        taken=taken,
        limit=5,
    )
    assert len(second) == 5 and all("미중" not in s["title"] for s in second)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (kst(2026, 11, 5, 6, 59), kst(2026, 11, 5, 7)),
        (kst(2026, 11, 5, 7, 0), kst(2026, 11, 5, 12)),
        (kst(2026, 11, 5, 12, 30), kst(2026, 11, 5, 17)),
        (kst(2026, 11, 5, 17, 30), kst(2026, 11, 6, 7)),
        (kst(2026, 11, 7, 23, 0), kst(2026, 11, 8, 7)),  # 주말도 매일
    ],
)
def test_next_slot(now: datetime, expected: datetime) -> None:
    assert next_slot(now) == expected


# ====================================================================== 갱신 (서비스)
@respx.mock
async def test_refresh_without_llm_keeps_article_list() -> None:
    route = respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
    r, db = room(SimulatedClock(NOW))
    ed = await r.refresh("schedule", slot=kst(2026, 11, 5, 7))
    queries = sum(len(s.queries) for s in SECTIONS)
    assert route.call_count == queries
    assert route.calls[0].request.url.params["sort"] == "date"
    assert [s["key"] for s in ed["sections"]] == ["world", "global_econ", "kr_market", "kr_issues"]
    world, glob, kr, issues = ed["sections"]
    assert [i["title"] for i in world["items"]] == [
        "미중 정상회담 개최…관세 협상 진전",
        "유럽 의회 선거 결과 발표",
    ]
    assert world["items"][0]["related"] == 2
    assert [i["title"] for i in kr["items"]] == ["코스피 외국인 매수에 상승 출발"]  # 미중 기사는 앞 섹션에
    assert glob["items"][0]["title"] == "뉴욕증시 기술주 강세 마감"
    assert issues["items"][0]["title"] == "[속보] 국회 본회의 예산안 처리"
    assert ed["headline"] is None and ed["summary_model"] is None and world["summary"] is None
    assert ed["slot"] == "07:00" and ed["trigger"] == "schedule"
    assert ed["stats"] == {"queries": queries, "failed_queries": 0, "articles": 5}
    view = r.view(auto=True)
    assert view["edition"]["id"] == ed["id"] and view["updated_at"] == ed["created_at"]
    assert view["next_update_at"] == "2026-11-05T12:00:00+09:00"
    assert view["auto"] is True and view["available"] is True and view["last_error"] is None
    assert view["llm"] == {"configured": False, "model": None}


@respx.mock
async def test_refresh_with_active_llm_summary_and_usage() -> None:
    respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return llm_reply(
            '```json\n{"headline": "미중 정상회담이 시장의 중심", "sections": '
            '{"world": "미중 정상이 만나 관세를 논의했다.", "kr_market": "코스피가 올랐다.", "x": "무시"}}\n```'
        )

    respx.post(f"{LLM_BASE}/chat/completions").mock(side_effect=handler)
    r, db = room(SimulatedClock(NOW), llm=fast_llm())
    ed = await r.refresh("manual")
    assert ed["headline"] == "미중 정상회담이 시장의 중심" and ed["summary_model"] == "news-model"
    by = {s["key"]: s for s in ed["sections"]}
    assert by["world"]["summary"] == "미중 정상이 만나 관세를 논의했다."
    assert by["global_econ"]["summary"] is None and ed["summary_error"] is None
    prompt = seen[0]["messages"][1]["content"]
    assert "미중 정상회담 개최…관세 협상 진전" in prompt and "2026-11-05 07:00 KST" in prompt
    with db.session() as s:
        usage = s.query(LlmUsage).one()
        assert (usage.kind, usage.model, usage.input_tokens, usage.run_id) == (
            "newsroom",
            "news-model",
            1500,
            None,
        )


@respx.mock
async def test_llm_failure_or_summary_off_still_saves_articles() -> None:
    respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
    llm_route = respx.post(f"{LLM_BASE}/chat/completions").mock(return_value=httpx.Response(500, text="boom"))
    clock = SimulatedClock(NOW)
    r, _db = room(clock, llm=fast_llm())
    ed = await r.refresh("manual")
    assert ed["summary_error"] and "요약 실패" in ed["summary_error"]
    assert ed["headline"] is None and ed["stats"]["articles"] == 5
    # 요약을 끄면 LLM 을 부르지 않는다
    assert r.set_summary(False) == {"summary": False}
    calls = llm_route.call_count
    clock.advance(timedelta(minutes=5))
    ed2 = await r.refresh("manual")
    assert llm_route.call_count == calls and ed2["summary_error"] is None and ed2["headline"] is None


@respx.mock
async def test_all_searches_fail_keeps_previous_edition() -> None:
    clock = SimulatedClock(NOW)
    r, _db = room(clock)
    respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
    first = await r.refresh("schedule", slot=kst(2026, 11, 5, 7))
    respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW, fail=set()))
    clock.set(kst(2026, 11, 5, 12, 0, 1))
    with pytest.raises(RuntimeError, match="뉴스를 하나도 받지 못했어요"):
        await r.refresh("schedule", slot=kst(2026, 11, 5, 12))
    view = r.view(auto=True)
    assert view["edition"]["id"] == first["id"]  # 이전 판 유지
    assert view["last_error"] and "HTTP 400" in view["last_error"]
    assert view["last_attempt"] == kst(2026, 11, 5, 12, 0, 1).isoformat()


@respx.mock
async def test_some_searches_fail_are_noted_per_section() -> None:
    respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW, fail={"코스피"}))
    r, _db = room(SimulatedClock(NOW))
    ed = await r.refresh("manual")
    kr = next(s for s in ed["sections"] if s["key"] == "kr_market")
    assert kr["errors"] == ["'코스피' 검색 실패"] and kr["items"] == []
    assert ed["stats"]["failed_queries"] == 1


async def test_old_editions_are_pruned_and_scheduled_skips_after_manual() -> None:
    clock = SimulatedClock(NOW)
    r, db = room(clock)
    with db.session() as s:
        s.add(NewsEdition(created_at=NOW - timedelta(days=20), trigger="schedule", payload={}))
    with respx.mock:
        respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
        await r.refresh("manual")
        with db.session() as s:
            assert s.query(NewsEdition).count() == 1  # 14일 넘은 판은 지운다
        # 07:00 슬롯 뒤에 이미 만든 판이 있으면 그 슬롯 작업은 건너뛴다
        assert await r.scheduled(kst(2026, 11, 5, 7)) is None


# ====================================================================== 분배기 07·12·17시
async def test_dispatcher_runs_news_room_daily_once_per_slot(tmp_path: Path) -> None:
    sat = kst(2026, 11, 7, 7, 0, 1)  # 토요일 — 휴장일에도 돈다
    with respx.mock, _client(tmp_path, sat, auth_enabled=False, **KEYS) as c:
        respx.get(naver.HUB_URL).mock(side_effect=naver_handler(sat))
        kr = c.app.state.container  # type: ignore[attr-defined]
        assert [j.name for j in kr.scheduler.daily] == ["newsroom"]
        assert await kr.scheduler.tick(wait=True) == ["2026-11-07 07:00 newsroom"]
        assert await kr.scheduler.tick(wait=True) == []  # 같은 슬롯은 한 번만
        assert "2026-11-07 07:00 newsroom" in kr.kv.get("sched_fired")["2026-11-07"]
        body = c.get("/api/news-room").json()
        assert body["edition"]["slot"] == "07:00" and body["edition"]["trigger"] == "schedule"
        assert body["next_update_at"] == "2026-11-07T12:00:00+09:00"
        kr.clock.set(kst(2026, 11, 7, 11, 59))
        assert await kr.scheduler.tick(wait=True) == []
        kr.clock.set(kst(2026, 11, 7, 12, 0, 1))
        assert await kr.scheduler.tick(wait=True) == ["2026-11-07 12:00 newsroom"]
        assert c.get("/api/news-room").json()["edition"]["slot"] == "12:00"


async def test_dispatcher_catches_up_latest_missed_slot_only(tmp_path: Path) -> None:
    late = kst(2026, 11, 7, 16, 10)  # 07:00·12:00 을 놓친 채 켜짐 → 12:00 하나만
    with respx.mock, _client(tmp_path, late, auth_enabled=False, **KEYS) as c:
        respx.get(naver.HUB_URL).mock(side_effect=naver_handler(late))
        kr = c.app.state.container  # type: ignore[attr-defined]
        assert await kr.scheduler.tick(wait=True) == ["2026-11-07 12:00 newsroom"]
        assert await kr.scheduler.tick(wait=True) == []
        kr.clock.set(kst(2026, 11, 8, 2, 0))  # 자정 넘어 새벽: 전날 17:00 슬롯
        assert await kr.scheduler.tick(wait=True) == ["2026-11-07 17:00 newsroom"]


async def test_no_news_keys_no_daily_job(tmp_path: Path) -> None:
    with _client(tmp_path, kst(2026, 11, 7, 7, 0, 1), auth_enabled=False) as c:
        kr = c.app.state.container  # type: ignore[attr-defined]
        assert kr.scheduler.daily == []
        assert await kr.scheduler.tick(wait=True) == []


# ====================================================================== API
def test_api_without_news_keys_still_answers(tmp_path: Path) -> None:
    with _client(tmp_path, NOW, auth_enabled=False) as c:
        body = c.get("/api/news-room").json()
        assert body["edition"] is None and body["updated_at"] is None
        assert body["available"] is False and body["auto"] is False
        assert "NAVER_CLIENT_ID" in body["reason"]
        assert body["schedule"] == ["07:00", "12:00", "17:00"] and body["timezone"] == "Asia/Seoul"
        assert body["next_update_at"] == "2026-11-05T12:00:00+09:00"
        assert [s["key"] for s in body["sections"]] == ["world", "global_econ", "kr_market", "kr_issues"]
        r = c.post("/api/news-room/refresh")
        assert r.status_code == 400 and "NAVER_CLIENT_ID" in r.json()["detail"]
        assert c.get("/api/us/news-room").status_code == 404  # 시장별 API 가 아니다


def test_api_requires_login(tmp_path: Path) -> None:
    with _client(tmp_path, NOW) as c:
        assert c.get("/api/news-room").status_code == 401
        assert c.post("/api/news-room/refresh").status_code == 401


def test_api_manual_refresh_in_background_and_settings(tmp_path: Path) -> None:
    with respx.mock, _client(tmp_path, NOW, auth_enabled=False, **KEYS) as c:
        respx.get(naver.HUB_URL).mock(side_effect=naver_handler(NOW))
        r = c.post("/api/news-room/refresh")
        assert r.status_code == 202 and r.json()["refreshing"] is True  # 잠금을 잡기 전에도 '모으는 중'
        end = _time.time() + 10
        body = r.json()
        while _time.time() < end and (body["refreshing"] or body["edition"] is None):
            _time.sleep(0.05)
            body = c.get("/api/news-room").json()
        assert body["edition"]["trigger"] == "manual" and body["edition"]["slot"] is None
        assert body["edition"]["stats"]["articles"] == 5 and body["refreshing"] is False
        assert body["settings"] == {"summary": True}
        again = c.post("/api/news-room/refresh")  # 방금 갱신 → 1분 안에는 다시 못 한다
        assert again.status_code == 409 and "초 뒤에 다시" in again.json()["detail"]
        s = c.put("/api/news-room/settings", json={"summary": False})
        assert s.status_code == 200 and s.json()["settings"] == {"summary": False}
        assert c.get("/api/news-room").json()["settings"] == {"summary": False}
        ev = c.get("/api/events?type=NEWS").json()
        assert ev and ev[0]["message"].startswith("뉴스 룸 수동 갱신 — 기사 5건")


# ====================================================================== 마이그레이션 0005
def test_migration_0005_up_down_sqlite(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from app.core.config import BACKEND_DIR

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "app" / "db" / "migrations"))
    cfg.attributes["url"] = url

    def tables() -> set[str]:
        eng = create_engine(url)
        try:
            return set(inspect(eng).get_table_names())
        finally:
            eng.dispose()

    command.upgrade(cfg, "head")
    assert "news_editions" in tables()
    eng = create_engine(url)
    assert {i["name"] for i in inspect(eng).get_indexes("news_editions")} == {"ix_news_editions_created_at"}
    eng.dispose()
    command.downgrade(cfg, "0004")
    assert "news_editions" not in tables()
    command.upgrade(cfg, "head")
    assert "news_editions" in tables()


def test_module_constants() -> None:
    assert [f"{t:%H:%M}" for t in newsroom.SLOTS] == ["07:00", "12:00", "17:00"]
    assert len({s.key for s in SECTIONS}) == len(SECTIONS) == 4
