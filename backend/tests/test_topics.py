"""대화에서 자라는 기억 (D-67): 되풀이하면 오르고, 한 번이면 빠지고, 매니저 판단·고정·빼기·잊기 (가짜 LLM 만)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.agent import topics
from app.agent.llm import ChatResult
from app.core.clock import KST
from app.db.models import ChatMessage, LlmUsage, MemoryTopic
from tests.fake_toss import at
from tests.helpers import make_container
from tests.test_api import _client

D = date(2026, 11, 2)  # 월요일
LLM = {"llm_base_url": "https://llm.test/v1", "llm_model": "test-model", "llm_api_key": "sk-test-123456"}


# ---------------------------------------------------------------- 중요도 (순수 함수)
def test_one_off_enters_today_and_drops_next_day() -> None:
    once = [D]
    assert topics.score(once, D) == 0.4 >= topics.THRESHOLD  # 그날은 들어간다
    assert topics.score(once, D + timedelta(days=1)) < topics.THRESHOLD  # 다음 날 빠진다


def test_recurring_topic_gains_importance_and_lasts() -> None:
    two = [D, D + timedelta(days=1)]  # 다음 날 또 물었다
    after = D + timedelta(days=2)
    assert topics.score(two, after) >= topics.THRESHOLD > topics.score([D + timedelta(days=1)], after)
    assert topics.score(two, D + timedelta(days=5)) < topics.THRESHOLD  # 그 뒤 안 물으면 며칠 안에 빠진다
    four = [D + timedelta(days=i) for i in range(4)]  # 나흘 연달아
    week_later = four[-1] + timedelta(days=7)
    assert topics.score(four, week_later) >= topics.THRESHOLD  # 일주일 뒤에도 남는다
    assert topics.score(four, four[-1]) > topics.score(two, two[-1]) > topics.score(once := [D], D)
    assert once  # (위 비교에서 만든 이름)
    # 연달아 묻는 것이 드문드문 묻는 것보다 조금 더 높다 (같은 날 수)
    spread = [D, D + timedelta(days=4), D + timedelta(days=8)]
    streak = [D + timedelta(days=6), D + timedelta(days=7), D + timedelta(days=8)]
    assert topics.score(streak, D + timedelta(days=8)) > topics.score(spread, D + timedelta(days=8))
    assert topics.streak(streak) == 3 and topics.streak(spread) == 1


def test_window_llm_level_and_forget() -> None:
    old = [D - timedelta(days=30)]
    assert topics.score(old, D) == 0.0  # 21일보다 오래된 언급은 세지 않는다
    assert topics.score([D], D, llm_level=1) == 0.6
    assert topics.score([D], D, llm_level=-1) == 0.2 < topics.THRESHOLD  # 매니저가 내리면 그날도 빠진다
    assert topics.score([D, D + timedelta(days=1)], D + timedelta(days=1), forgotten=True) == 0.0


# ---------------------------------------------------------------- LLM 답 읽기
def test_parse_review_needs_evidence_and_known_keys() -> None:
    known = {"t:반도체": "반도체"}
    names = {"035720": "카카오"}
    text = json.dumps(
        {
            "mentions": [
                {"key": "", "label": "카카오", "symbol": "035720", "message_ids": [10, 11]},
                {"key": "t:반도체", "label": "반도체", "message_ids": [11]},
                {"key": "", "label": "지어낸 주제", "symbol": None, "message_ids": [999]},  # 근거 없음 → 버림
                {
                    "key": "",
                    "label": "가짜코드",
                    "symbol": "999999",
                    "message_ids": [10],
                },  # 없는 코드 → 이름만
                {
                    "key": "",
                    "label": "반도체",
                    "message_ids": ["#12"],
                },  # 같은 이름 → 있던 주제로 합친다 (#id 도 받음)
            ],
            "adjust": [
                {"key": "t:반도체", "action": "promote", "reason": "여러 날 물음"},
                {"key": "", "label": "카카오(035720)", "action": "demote", "reason": "한 번 묻고 끝"},
                {"key": "t:없는주제", "action": "forget", "reason": "x"},  # 모르는 key → 버림
                {"key": "t:반도체", "action": "explode", "reason": "x"},  # 모르는 행동 → 버림
            ],
        },
        ensure_ascii=False,
    )
    out = topics.parse_review(text, {10, 11, 12}, known, names.get)
    assert out is not None
    mentions, adjusts = out
    by = {m.key: m for m in mentions}
    assert set(by) == {"sym:035720", "t:반도체", "t:가짜코드"}
    assert by["sym:035720"].label == "카카오(035720)" and by["sym:035720"].message_ids == [10, 11]
    assert by["t:반도체"].message_ids == [11, 12] and by["t:가짜코드"].symbol is None
    assert [(a.key, a.action) for a in adjusts] == [("t:반도체", "promote"), ("sym:035720", "demote")]
    assert topics.parse_review("요약할게 없어요", {1}, {}) is None


# ---------------------------------------------------------------- 매니저와 함께
def _say(c: Any, text: str, ts: datetime) -> int:
    with c.db.session() as s:
        m = ChatMessage(ts=ts, role="user", kind="text", content=text, payload={})
        s.add(m)
        s.flush()
        return int(m.id)


def _fake_llm(c: Any, answers: list[dict[str, Any] | str], seen: list[str] | None = None) -> None:
    queue = list(answers)

    async def chat(messages: list[dict[str, Any]], *a: Any, **k: Any) -> ChatResult:
        if seen is not None:
            seen.append(messages[-1]["content"])
        ans = queue.pop(0)
        body = ans if isinstance(ans, str) else json.dumps(ans, ensure_ascii=False)
        return ChatResult(
            content=body, usage={"prompt_tokens": 800, "completion_tokens": 60, "total_tokens": 860}
        )

    c.runner.llm.chat = chat


def _mention(mid: int, label: str = "카카오", key: str = "", action: str = "keep") -> dict[str, Any]:
    return {
        "mentions": [{"key": key, "label": label, "symbol": None, "message_ids": [mid]}],
        "adjust": [{"key": key, "label": label, "action": action, "reason": f"{action} 이유"}],
    }


async def test_recurring_question_stays_and_one_off_fades(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 9), **LLM)
    seen: list[str] = []
    m1 = _say(c, "카카오는 어때?", at(D, 8))
    _fake_llm(c, [_mention(m1)], seen)
    r = await c.runner.review_topics(7)
    assert r["messages"] == 1 and r["created"] == 1
    assert f"#{m1}" in seen[0] and "카카오는 어때?" in seen[0]
    assert "카카오" in c.runner._memory()  # 그날은 들어간다
    with c.db.session() as s:
        usage = [(u.kind, u.run_id) for u in s.scalars(select(LlmUsage))]
    assert usage == [("memory", 7)]  # 요금 장부에 '기억 정리'로

    c.runner.clock.set(at(D + timedelta(days=1), 9))
    assert "카카오" not in c.runner._memory()  # 한 번 묻고 만 주제는 다음 날 빠진다
    m2 = _say(c, "카카오 오늘은?", at(D + timedelta(days=1), 8, 30))
    _fake_llm(c, [_mention(m2, key="t:카카오", action="promote")], seen)
    await c.runner.review_topics()
    assert (
        f"#{m1}" not in seen[1] and "t:카카오 · 카카오 · 1일" in seen[1]
    )  # 이미 정리한 메시지는 다시 안 보낸다
    t = topics.all_topics(c.db, D + timedelta(days=1))[0]
    assert t["days"] == 2 and t["streak"] == 2 and t["mentions"] == 2 and t["llm_reason"] == "promote 이유"
    c.runner.clock.set(at(D + timedelta(days=3), 9))
    snap = c.runner.memory_snapshot()
    assert [x["label"] for x in snap["topics"]] == ["카카오"]  # 다음 날 또 물었으니 이틀 뒤에도 남는다
    assert "투자자가 대화에서 되풀이한 관심사" in c.runner._memory()
    assert await c.runner.review_topics() == {
        "messages": 0,
        "mentioned": 0,
        "created": 0,
        "adjusted": 0,
        "dropped": 0,
        "skipped": "새로 정리할 메시지가 없어요",
    }


async def test_forget_pin_mute_and_disabled(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 9), **LLM)
    a = _say(c, "삼성전자 계속 봐줘", at(D, 8))
    _fake_llm(c, [_mention(a, "삼성전자", action="promote")])
    await c.runner.review_topics()
    assert "삼성전자" in c.runner._memory()
    b = _say(c, "삼성전자는 이제 그만 잊어줘.", at(D, 8, 30))
    _fake_llm(c, [_mention(b, "삼성전자", key="t:삼성전자", action="forget")])
    await c.runner.review_topics()
    t = topics.all_topics(c.db, D)[0]
    assert t["forgotten"] and t["score"] == 0.0 and not t["included"]
    assert "삼성전자" not in c.runner._memory()
    # 잊으라고 한 뒤 다시 꺼내면 다시 센다
    c.runner.clock.set(at(D + timedelta(days=1), 9))
    d = _say(c, "삼성전자 다시 보자", at(D + timedelta(days=1), 8))
    _fake_llm(c, [_mention(d, "삼성전자", key="t:삼성전자")])
    await c.runner.review_topics()
    assert not topics.all_topics(c.db, D + timedelta(days=1))[0]["forgotten"]

    # 고정은 오래돼도 들어가고, 빼기는 중요해도 안 들어간다
    tid = topics.all_topics(c.db, D)[0]["id"]
    topics.set_pin(c.db, tid, "pin")
    c.runner.clock.set(at(D + timedelta(days=15), 9))
    assert "삼성전자" in c.runner._memory() and "늘 기억" in c.runner._memory()
    topics.set_pin(c.db, tid, "mute")
    c.runner.clock.set(at(D + timedelta(days=1), 10))
    assert "삼성전자" not in c.runner._memory()
    topics.set_pin(c.db, tid, "")
    topics.set_enabled(c.runner.settings, False)
    assert "삼성전자" not in c.runner._memory()
    _say(c, "새 메시지", at(D + timedelta(days=1), 9, 30))
    await c.runner._review_before_session(1)  # 꺼 두면 정리하지 않는다 (LLM 호출 없음)
    assert topics.review_state(c.runner.settings)["last_msg_id"] == d


async def test_bad_answers_are_retried_then_skipped(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 9), **LLM)
    m = _say(c, "반도체 업황 어때?", at(D, 8))
    _fake_llm(c, ["JSON 아님", "또 아님", "역시 아님"])
    for i in range(3):
        r = await c.runner.review_topics()
        assert r["skipped"] == "정리 결과를 읽지 못했어요"
        assert topics.review_state(c.runner.settings)["last_msg_id"] == (m if i == 2 else 0)
    assert (
        topics.pending(c.db, c.runner.settings, c.runner.clock.now(), KST) == []
    )  # 같은 호출을 계속 반복하지 않게


async def test_session_reviews_first_only_when_new_messages(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 9), **LLM)
    calls: list[int | None] = []

    async def fake_review(run_id: int | None = None) -> dict[str, Any]:
        calls.append(run_id)
        return {}

    c.runner.review_topics = fake_review
    await c.runner._review_before_session(3)
    assert calls == []  # 새 메시지 없으면 부르지 않는다
    _say(c, "현금 비중 늘려줘", at(D, 8))
    await c.runner._review_before_session(4)
    assert calls == [4]


def test_topics_api_and_structure(tmp_path: Path) -> None:
    now = datetime(2026, 11, 4, 10, tzinfo=KST)
    with _client(tmp_path, now, auth_enabled=False) as client:
        empty = client.get("/api/memory/topics").json()
        assert empty["topics"] == [] and empty["settings"]["topics"] is True and empty["llm"] is False
        assert client.post("/api/memory/topics/review").status_code == 400  # LLM 없으면 정리 못 함
        cont = client.app.state.container  # type: ignore[attr-defined]
        with cont.db.session() as s:
            s.add(
                MemoryTopic(
                    key="t:반도체",
                    label="반도체",
                    days=["2026-11-03", "2026-11-04"],
                    mentions=2,
                    quotes=[{"id": 1, "at": now.isoformat(), "text": "반도체 어때?"}],
                    first_seen=now - timedelta(days=1),
                    last_seen=now,
                    llm_level=1,
                    llm_reason="이틀 연달아 물음",
                    pin="",
                )
            )
        body = client.get("/api/memory/topics").json()
        t = body["topics"][0]
        assert (
            t["label"] == "반도체"
            and t["included"]
            and t["days"] == 2
            and t["llm_reason"] == "이틀 연달아 물음"
        )
        r = client.patch(f"/api/memory/topics/{t['id']}", json={"pin": "mute"})
        assert r.status_code == 200 and r.json()["included"] is False
        assert client.patch(f"/api/memory/topics/{t['id']}", json={"pin": "x"}).status_code == 400
        assert client.patch("/api/memory/topics/999", json={"pin": "pin"}).status_code == 404
        st = client.get("/api/agent/structure").json()["memory"]
        assert st["topics"] == [] and st["use_topics"] is True
        rule = next(x for x in st["rules"] if x["name"] == "대화에서 되풀이한 관심사")
        assert "지금 켜짐" in rule["detail"]
        assert client.put("/api/memory/settings", json={"topics": False}).json()["topics"] is False
        assert client.get("/api/memory/cards").json()["settings"]["topics"] is False
        assert client.get("/api/us/memory/topics").json()["settings"]["topics"] is True  # 시장마다 따로
        ev = [e["message"] for e in client.get("/api/events?type=CONFIG").json()]
        assert any(m == "기억 주제 빼기: 반도체" for m in ev) and any("대화에서 기억하기 끔" in m for m in ev)
        assert client.delete(f"/api/memory/topics/{t['id']}").status_code == 204
        assert client.get("/api/memory/topics").json()["topics"] == []
