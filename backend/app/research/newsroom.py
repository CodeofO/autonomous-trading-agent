"""뉴스 룸 (D-56) — 세계의 흐름과 대한민국 주요 뉴스를 섹션별로 모아 한 판으로 저장한다.

- 기사: 네이버 뉴스 검색(`NaverNewsClient`, NAVER API HUB/개발자센터, 키는 `.env`). 섹션마다 검색어 몇 개를 최신순으로
  받아 지난 24시간 기사만 남기고, 같은 사건을 다룬 기사(제목 단어가 많이 겹침)를 하나로 묶는다. 여러 매체가 다룬
  기사일수록 위로 올린다(묶인 기사 수 → 최신순). 앞 섹션에 나온 사건은 뒤 섹션에서 뺀다.
- 요약(선택): 지금 활성 LLM 프로필(국장 매니저와 같은 것)로 '오늘의 흐름' 한 줄과 섹션별 2~3문장. LLM 이 없거나
  실패하면 요약 없이 기사 목록만 저장한다. 화면에서 끌 수 있다(kv `news_room.summary`).
- 증시 영향 3줄(D-59): 같은 LLM 호출에서 섹션마다 증시에 영향이 클 것 같은 이슈 3줄(+근거 기사 번호)을 받아 판에 함께
  저장한다(화면을 열 때 LLM 을 부르지 않는다). LLM 이 없거나 실패·빠뜨리면 규칙 기반(증시 관련 말이 많은 기사 제목
  3개, `source: "rule"` = 화면의 'AI 요약 아님'). 예전 판은 읽을 때 규칙 기반으로 채운다.
- 구글 검색(Gemini 그라운딩)은 쓰지 않는다: 약관상 그라운딩 결과는 Search Suggestions 와 함께 '질문한 사용자'에게만
  보여 줘야 하고, 캐시·수정·다른 내용과 섞기가 금지라 미리 모아 저장해 두는 뉴스 룸과 맞지 않는다.
- 일정: 매일 07:00·12:00·17:00(한국 시각, 주말·휴장일 포함). 국장 1분 분배기(`Scheduler.daily`)가 `sched_fired`
  기록으로 한 번만 돌린다. 서버가 꺼져 있어 놓친 경우 켜진 뒤 가장 최근 슬롯 하나만 따라잡는다.
"""

from __future__ import annotations

import asyncio
import copy
import email.utils
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.agent.costs import record_call
from app.agent.llm import LLMClient, extract_json
from app.core.clock import KST, Clock
from app.db.models import NewsEdition
from app.db.session import Database
from app.engine.settings_store import SettingsStore
from app.research.naver import NaverNewsClient

log = logging.getLogger(__name__)

SLOTS: tuple[time, ...] = (time(7, 0), time(12, 0), time(17, 0))  # 한국 시각, 매일
KV = "news_room"  # {summary: bool, last_error, last_attempt}
KEEP_DAYS = 14  # 이보다 오래된 판은 지운다
PER_QUERY = 20  # 검색어 하나당 받는 기사 수 (네이버 display 최대 100)
PER_SECTION = 5  # 섹션마다 보여 주는 기사 수 (최대)
MAX_AGE = timedelta(hours=24)  # 이보다 오래된 기사는 뺀다
SAME_STORY = 0.45  # 제목 단어 자카드 유사도가 이 이상이면 같은 사건으로 묶는다
MANUAL_COOLDOWN = timedelta(seconds=60)  # 수동 새로고침 최소 간격 (유료 LLM 연타 방지)
SUMMARY_MAX = 400  # 섹션 요약 최대 글자
HEADLINE_MAX = 120
IMPACT_LINES = 3  # 섹션마다 '증시 영향' 몇 줄 (D-59)
IMPACT_MAX = 90  # 한 줄 최대 글자
# 규칙 기반 '증시 영향'(LLM 없음·실패·요약 끔) 순위에 쓰는 말 — 제목·요약문에 많이 나올수록 위로
MARKET_WORDS: tuple[str, ...] = (
    "증시", "주가", "코스피", "코스닥", "나스닥", "다우", "s&p", "금리", "연준", "fomc", "환율", "달러",
    "유가", "원유", "관세", "무역", "수출", "실적", "반도체", "인플레이션", "물가", "고용", "국채", "채권",
    "외국인", "매수", "매도", "부동산", "예산", "규제", "제재", "전쟁", "휴전", "경기", "성장률",
)  # fmt: skip


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    icon: str
    queries: tuple[str, ...]


SECTIONS: tuple[Section, ...] = (
    Section("world", "세계·국제", "🌍", ("국제 정세", "외신", "정상회담")),
    Section("global_econ", "미국·글로벌 경제", "🌐", ("뉴욕증시", "연준 금리", "국제유가", "달러 환율")),
    Section("kr_market", "국내 경제·증시", "📈", ("코스피", "코스닥", "한국은행", "원달러 환율")),
    Section("kr_issues", "국내 주요 이슈", "🇰🇷", ("속보", "대통령실", "국회")),
)

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|<[^>]*>")
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


class NewsRoomBusyError(RuntimeError):
    pass


class NewsRoomUnavailableError(RuntimeError):
    pass


# ====================================================================== 기사 고르기
def tokens(title: str) -> frozenset[str]:
    """같은 사건 판단용 제목 단어 ([속보]·[단독] 같은 머리표는 뺀다)."""
    return frozenset(w for w in _WORD.findall(_BRACKETS.sub(" ", title).lower()) if len(w) >= 2)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= SAME_STORY


def published_at(raw: str) -> datetime | None:
    """네이버 pubDate (RFC 822, 예: 'Thu, 05 Nov 2026 07:10:00 +0900')."""
    try:
        t = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=KST)


def domain(link: str) -> str:
    host = (urlsplit(link).hostname or "").lower()
    return host.removeprefix("www.")


@dataclass
class _Story:
    item: dict[str, Any]
    words: frozenset[str]
    at: datetime | None
    related: int = 1
    domains: set[str] = field(default_factory=set)


def pick_stories(
    articles: list[dict[str, Any]],
    now: datetime,
    *,
    taken: list[frozenset[str]] | None = None,
    limit: int = PER_SECTION,
) -> list[dict[str, Any]]:
    """기사 목록 → 섹션에 보일 기사. 같은 사건은 하나로(가장 최근 기사), 많이 다뤄진 사건 → 최신 순.

    taken: 앞 섹션에서 이미 고른 사건의 제목 단어 — 겹치면 뺀다. 고른 것을 여기에 더한다.
    """
    taken = taken if taken is not None else []
    seen_links: set[str] = set()
    rows: list[tuple[dict[str, Any], frozenset[str], datetime | None]] = []
    for a in articles:
        link, title = str(a.get("link") or ""), str(a.get("title") or "").strip()
        if not title or not link or link in seen_links:
            continue
        seen_links.add(link)
        at = published_at(str(a.get("pubDate") or ""))
        if at is not None and (now - at > MAX_AGE or at - now > timedelta(hours=1)):
            continue
        rows.append((a, tokens(title), at))
    # 최신 기사가 그 사건의 대표가 되도록 최신순으로 묶는다 (시각 없는 기사는 맨 뒤)
    rows.sort(key=lambda r: r[2] or datetime.min.replace(tzinfo=UTC), reverse=True)
    stories: list[_Story] = []
    for a, words, at in rows:
        if any(similar(words, t) for t in taken):
            continue
        hit = next((s for s in stories if similar(words, s.words)), None)
        d = domain(str(a.get("link") or ""))
        if hit is not None:
            hit.related += 1
            hit.domains.add(d)
            continue
        stories.append(_Story(a, words, at, domains={d}))
    oldest = datetime.min.replace(tzinfo=UTC)
    stories.sort(key=lambda s: (len(s.domains), s.related, s.at or oldest), reverse=True)
    out: list[dict[str, Any]] = []
    for s in stories[:limit]:
        taken.append(s.words)
        link = str(s.item.get("link") or "")
        out.append(
            {
                "title": str(s.item.get("title") or "")[:200],
                "description": str(s.item.get("description") or "")[:300],
                "link": link,
                "domain": domain(link),
                "published_at": s.at.isoformat() if s.at else None,
                "related": s.related,
            }
        )
    return out


# ====================================================================== 증시 영향 3줄 (D-59)
def market_score(item: dict[str, Any]) -> int:
    text = f"{item.get('title', '')} {item.get('description', '')}".lower()
    return sum(w in text for w in MARKET_WORDS)


def rule_impact(items: list[dict[str, Any]]) -> dict[str, Any]:
    """LLM 없이: 증시 관련 말이 많이 든 기사 → 많이 다뤄진 기사 → 원래 순서로 3개, 제목 그대로 ('AI 요약 아님')."""
    ranked = sorted(
        enumerate(items), key=lambda p: (-market_score(p[1]), -int(p[1].get("related") or 1), p[0])
    )
    lines = [
        {"text": str(it["title"])[:IMPACT_MAX], "link": it.get("link") or None, "title": it["title"]}
        for _i, it in ranked[:IMPACT_LINES]
    ]
    return {"source": "rule", "lines": lines}


def fill_impact(sections: list[dict[str, Any]]) -> None:
    """'증시 영향'이 없는 섹션(LLM 이 빠뜨림·요약 없음·예전 판)을 규칙 기반으로 채운다."""
    for s in sections:
        if not isinstance(s.get("impact"), dict):
            s["impact"] = rule_impact(s.get("items") or [])


def _impact_line(x: Any, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """LLM 이 준 한 줄({text, ref} 또는 글자) → {text, link, title}. 글자가 없으면 None."""
    text, ref = (x.get("text"), x.get("ref")) if isinstance(x, dict) else (x, None)
    if not isinstance(text, str) or not text.strip():
        return None
    if isinstance(ref, str) and ref.strip().isdigit():
        ref = int(ref)
    item = None
    if isinstance(ref, int) and not isinstance(ref, bool) and 1 <= ref <= len(items):
        item = items[ref - 1]
    return {
        "text": text.strip()[:IMPACT_MAX],
        "link": (item.get("link") or None) if item else None,
        "title": item["title"] if item else None,
    }


def parse_impact(raw: Any, sections: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """LLM 의 impact {섹션: [{text, ref}]} → 섹션별 줄. ref(기사 번호)가 맞으면 그 기사 링크를 붙인다."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for s in sections:
        got = raw.get(s["key"])
        if not isinstance(got, list):
            continue
        items = s.get("items") or []
        lines = [ln for x in got if (ln := _impact_line(x, items)) is not None][:IMPACT_LINES]
        if lines:
            out[s["key"]] = lines
    return out


# ====================================================================== 요약 (LLM, 선택)
SUMMARY_SYSTEM = (
    "너는 한국어 뉴스 데스크 편집자다. 주어진 기사 제목과 요약문만 근거로 쓴다. 기사에 없는 사실·숫자·전망을 "
    "지어내지 말고, 투자 권유나 매수·매도 의견은 쓰지 않는다. 반드시 JSON 객체 하나만 답한다."
)
SUMMARY_PROMPT = (
    "아래는 {when} 기준 섹션별 주요 기사다 (각 기사 앞 숫자가 그 섹션의 기사 번호).\n"
    "다음 형식의 JSON 으로 답하라:\n{schema}\n"
    "{headline_rule}"
    "- sections: 섹션 요약 각각 2~3문장, 200자 이내, 핵심 사건 위주. 기사가 없는 섹션은 빈 문자열\n"
    "- impact: 섹션마다 주식시장(국내·미국 증시)에 영향이 가장 클 것 같은 이슈 3개를 영향이 큰 순서로, "
    "각각 한 줄(60자 이내). ref 는 근거 기사 번호. 기사가 없는 섹션은 빈 배열. "
    "오르내림을 단정하거나 매수·매도를 권하지 말 것\n\n{body}"
)


HEADLINE_RULE = "- headline: 세계와 한국의 오늘 흐름 한 문장(80자 이내)\n"
HEADLINE_PROMPT = (
    "아래는 {when} 기준 섹션별 뉴스 요약이다. 세계와 한국의 오늘 흐름을 한 문장(80자 이내)으로 써라. "
    '요약에 없는 사실은 쓰지 말 것. JSON {{"headline": "…"}} 하나만 답한다.\n\n{body}'
)
# 섹션마다 따로 요약한다: 한 번에 4섹션을 맡기면 추론형 무료 모델(Qwen 등)이 생각에 출력 한도를 거의 다 써서
# 답(JSON)이 잘렸다 (2026-11-05 실측: 8,192 토큰 중 ~7,900 이 생각, 답 663자에서 끊김 → 섹션별은 3~4.5천 토큰에 끝남)
SECTION_TRIES = 2  # 답이 잘리거나 JSON 이 아니면 한 번 더
RETRY_TOKENS_FACTOR = (
    2  # 잘려서 다시 물을 때 출력 한도를 이만큼 늘린다 (생각 길이가 매번 달라 8,192 를 넘기기도 함)
)
SUMMARY_CONCURRENCY = 4


def summary_prompt(sections: list[dict[str, Any]], when: datetime, *, headline: bool = True) -> str:
    shape: dict[str, Any] = {"headline": "…"} if headline else {}
    shape["sections"] = {s["key"]: f"{s['title']} 요약" for s in sections}
    shape["impact"] = {s["key"]: [{"text": "증시 영향 한 줄", "ref": 1}] for s in sections}
    schema = json.dumps(shape, ensure_ascii=False)
    parts: list[str] = []
    for s in sections:
        parts.append(f"## {s['key']} — {s['title']}")
        for i, it in enumerate(s["items"], 1):
            desc = it["description"][:160]
            parts.append(f"{i}. {it['title']}" + (f" — {desc}" if desc else ""))
        if not s["items"]:
            parts.append("(기사 없음)")
    return SUMMARY_PROMPT.format(
        when=f"{when:%Y-%m-%d %H:%M} KST",
        schema=schema,
        headline_rule=HEADLINE_RULE if headline else "",
        body="\n".join(parts),
    )


class TruncatedError(ValueError):
    """LLM 답이 출력 한도에서 잘림 (추론형 모델이 생각에 한도를 다 씀)."""


def headline_prompt(sections: list[dict[str, Any]], summaries: dict[str, str], when: datetime) -> str:
    body = "\n".join(f"- {s['title']}: {summaries[s['key']]}" for s in sections if summaries.get(s["key"]))
    return HEADLINE_PROMPT.format(when=f"{when:%Y-%m-%d %H:%M} KST", body=body)


def parse_headline(text: str) -> str | None:
    data = extract_json(text or "")
    head = data.get("headline") if data else None
    return str(head).strip()[:HEADLINE_MAX] if isinstance(head, str) and head.strip() else None


def parse_summary(
    text: str, sections: list[dict[str, Any]], *, allow_empty: bool = False
) -> tuple[str | None, dict[str, str], dict[str, list[dict[str, Any]]]]:
    """(headline, 섹션 요약, 증시 영향 줄). JSON 이 아니면 ValueError, 쓸 내용이 없으면(allow_empty 아니면) ValueError."""
    keys = [s["key"] for s in sections]
    data = extract_json(text or "")
    if data is None:
        raise ValueError("요약 응답이 JSON 이 아니에요")
    head = data.get("headline")
    headline = str(head).strip()[:HEADLINE_MAX] if isinstance(head, str) and head.strip() else None
    raw = data.get("sections")
    secs = raw if isinstance(raw, dict) else {}
    out = {
        k: str(secs[k]).strip()[:SUMMARY_MAX]
        for k in keys
        if isinstance(secs.get(k), str) and secs[k].strip()
    }
    impact = parse_impact(data.get("impact"), sections)
    if headline is None and not out and not impact and not allow_empty:
        raise ValueError("요약 응답에 내용이 없어요")
    return headline, out, impact


# ====================================================================== 일정
def slot_label(t: time) -> str:
    return f"{t:%H:%M}"


def next_slot(now: datetime) -> datetime:
    """지금 이후 첫 갱신 시각 (한국 시각)."""
    local = now.astimezone(KST)
    for d in (local.date(), local.date() + timedelta(days=1)):
        for t in SLOTS:
            at = datetime.combine(d, t, KST)
            if at > local:
                return at
    raise AssertionError("unreachable")  # pragma: no cover


# ====================================================================== 서비스
class NewsRoom:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        kv: SettingsStore,
        *,
        news: NaverNewsClient | None,
        llm: Callable[[], LLMClient | None] = lambda: None,
        audit: Callable[..., Any] | None = None,
        sections: tuple[Section, ...] = SECTIONS,
    ) -> None:
        self.db = db
        self.clock = clock
        self.kv = kv
        self.news = news
        self.llm = llm  # 지금 활성 LLM (프로필을 바꾸면 매니저와 함께 바뀐다)
        self.audit = audit
        self.sections = sections
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ 상태
    @property
    def available(self) -> bool:
        return self.news is not None

    @property
    def unavailable_reason(self) -> str | None:
        if self.news is None:
            return "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정 — .env 에 네이버 뉴스 검색 키를 넣고 재시작하세요"
        return self.news.auth_error

    @property
    def refreshing(self) -> bool:
        """모으는 중 (수동 새로고침은 작업을 띄운 순간부터 — 잠금을 잡기 전에도 화면이 '모으는 중'을 보게)."""
        return self._lock.locked() or any(not t.done() for t in self._tasks)

    def settings(self) -> dict[str, Any]:
        return {"summary": bool(self.kv.get(KV).get("summary", True))}

    def set_summary(self, on: bool) -> dict[str, Any]:
        self.kv.set(KV, {**self.kv.get(KV), "summary": bool(on)})
        return self.settings()

    def latest(self) -> NewsEdition | None:
        with self.db.session() as s:
            row = s.scalar(select(NewsEdition).order_by(NewsEdition.created_at.desc(), NewsEdition.id.desc()))
            if row is not None:
                s.expunge(row)
            return row

    def view(self, *, auto: bool) -> dict[str, Any]:
        """`GET /api/news-room` 응답."""
        now = self.clock.now()
        row = self.latest()
        state = self.kv.get(KV)
        llm = self.llm()
        edition = None
        if row is not None:
            payload = copy.deepcopy(row.payload)
            for sec in payload.get("sections") or []:  # 예전 판(섹션당 8개)도 지금 한도까지만
                sec["items"] = list(sec.get("items") or [])[:PER_SECTION]
            fill_impact(payload.get("sections") or [])  # 증시 영향이 없는 예전 판(D-59 전)은 규칙 기반으로
            edition = {
                "id": row.id,
                "created_at": _iso(row.created_at),
                "trigger": row.trigger,
                **payload,
            }
        return {
            "edition": edition,
            "updated_at": edition["created_at"] if edition else None,
            "next_update_at": next_slot(now).isoformat(),
            "schedule": [slot_label(t) for t in SLOTS],
            "timezone": "Asia/Seoul",
            "auto": auto and self.available,
            "available": self.available,
            "reason": self.unavailable_reason,
            "refreshing": self.refreshing,
            "last_error": state.get("last_error"),
            "last_attempt": state.get("last_attempt"),
            "settings": self.settings(),
            "llm": {"configured": llm is not None, "model": llm.model if llm else None},
            "sections": [{"key": s.key, "title": s.title, "icon": s.icon} for s in self.sections],
        }

    # ------------------------------------------------------------ 갱신
    async def scheduled(self, slot: datetime) -> dict[str, Any] | None:
        """분배기가 부르는 07·12·17시 작업. 그 슬롯 뒤에 이미 만든 판(수동 새로고침)이 있으면 건너뛴다."""
        row = self.latest()
        if row is not None and row.created_at >= slot:
            log.info(
                "뉴스 룸 %s 갱신 건너뜀 — 이미 %s 에 만든 판이 있음",
                slot_label(slot.timetz()),
                row.created_at,
            )
            return None
        if self.refreshing:
            log.info("뉴스 룸 %s 갱신 건너뜀 — 다른 갱신 진행 중", slot_label(slot.timetz()))
            return None
        return await self.refresh("schedule", slot=slot)

    def start_manual(self) -> None:
        """수동 새로고침을 백그라운드로 시작한다 (LLM 요약은 수십 초 걸릴 수 있다). 화면은 refreshing 으로 기다린다."""
        if not self.available:
            raise NewsRoomUnavailableError(self.unavailable_reason or "뉴스 검색 키가 없어요")
        if self.refreshing:
            raise NewsRoomBusyError("이미 뉴스를 모으고 있어요")
        row = self.latest()
        now = self.clock.now()
        if row is not None and now - row.created_at < MANUAL_COOLDOWN:
            wait = int((MANUAL_COOLDOWN - (now - row.created_at)).total_seconds()) + 1
            raise NewsRoomBusyError(f"방금 갱신했어요. {wait}초 뒤에 다시 눌러 주세요")
        t = asyncio.create_task(self._manual())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _manual(self) -> None:
        try:
            await self.refresh("manual")
        except Exception:  # 오류는 kv last_error 에 남겼다
            log.exception("뉴스 룸 수동 갱신 실패")

    async def refresh(self, trigger: str, *, slot: datetime | None = None) -> dict[str, Any]:
        """기사를 모아(+요약) 한 판을 저장하고 돌려준다. 기사를 하나도 못 받으면 이전 판을 그대로 두고 예외."""
        if self.news is None:
            raise NewsRoomUnavailableError(self.unavailable_reason or "뉴스 검색 키가 없어요")
        async with self._lock:
            started = self.clock.now()
            try:
                payload = await self._build(trigger, started, slot)
            except Exception as e:
                msg = str(e)[:300] or type(e).__name__
                self._status(started, msg)
                if self.audit is not None:
                    self.audit("NEWS", f"뉴스 룸 갱신 실패: {msg}", None)
                raise
            with self.db.session() as s:
                row = NewsEdition(created_at=self.clock.now(), trigger=trigger, payload=payload)
                s.add(row)
                s.flush()
                edition = {"id": row.id, "created_at": _iso(row.created_at), "trigger": trigger, **payload}
                s.execute(
                    delete(NewsEdition).where(NewsEdition.created_at < started - timedelta(days=KEEP_DAYS))
                )
            self._status(started, None)
            stats = payload["stats"]
            when = f"{slot:%H:%M} " if slot else "수동 "
            log.info(
                "뉴스 룸 %s갱신: 기사 %d건(섹션 %d), 요약 %s",
                when,
                stats["articles"],
                len(payload["sections"]),
                payload.get("summary_model") or "없음",
            )
            if self.audit is not None:
                self.audit(
                    "NEWS",
                    f"뉴스 룸 {when}갱신 — 기사 {stats['articles']}건"
                    + (f", 요약 {payload['summary_model']}" if payload.get("summary_model") else ""),
                    None,
                    edition_id=edition["id"],
                )
            return edition

    def _status(self, at: datetime, error: str | None) -> None:
        self.kv.set(KV, {**self.kv.get(KV), "last_attempt": at.isoformat(), "last_error": error})

    async def _build(self, trigger: str, now: datetime, slot: datetime | None) -> dict[str, Any]:
        assert self.news is not None
        news = self.news
        jobs = [(s.key, q) for s in self.sections for q in s.queries]

        async def fetch(q: str) -> list[dict[str, Any]] | Exception:
            try:
                return await news.search(q, display=PER_QUERY, sort="date")
            except Exception as e:  # 검색어 하나가 실패해도 나머지로 판을 만든다
                return e

        results = await asyncio.gather(*(fetch(q) for _k, q in jobs))
        by_section: dict[str, list[dict[str, Any]]] = {s.key: [] for s in self.sections}
        errors: dict[str, list[str]] = {s.key: [] for s in self.sections}
        failed: list[str] = []
        for (key, q), res in zip(jobs, results, strict=True):
            if isinstance(res, Exception):
                failed.append(str(res)[:200])
                errors[key].append(f"'{q}' 검색 실패")
            else:
                by_section[key].extend(res)
        if len(failed) == len(jobs):
            raise RuntimeError(f"뉴스를 하나도 받지 못했어요: {failed[0]}")
        taken: list[frozenset[str]] = []
        sections: list[dict[str, Any]] = [
            {
                "key": s.key,
                "title": s.title,
                "icon": s.icon,
                "summary": None,
                "items": pick_stories(by_section[s.key], now, taken=taken),
                "errors": errors[s.key],
            }
            for s in self.sections
        ]
        total = sum(len(s["items"]) for s in sections)
        if total == 0:
            raise RuntimeError("지난 24시간 안의 기사를 찾지 못했어요")
        headline, model, summary_error = await self._summarize(sections, now)
        fill_impact(sections)  # LLM 이 없거나 빠뜨린 섹션은 규칙 기반 ('AI 요약 아님')
        return {
            "trigger": trigger,
            "slot": slot_label(slot.astimezone(KST).time()) if slot else None,
            "headline": headline,
            "summary_model": model,
            "summary_error": summary_error,
            "sections": sections,
            "sources": {"news": "NAVER"},
            "stats": {"queries": len(jobs), "failed_queries": len(failed), "articles": total},
        }

    async def _summarize(
        self, sections: list[dict[str, Any]], now: datetime
    ) -> tuple[str | None, str | None, str | None]:
        """(headline, 모델, 오류). 끔·LLM 없음이면 (None, None, None). 실패해도 예외를 내지 않는다.

        섹션마다 따로(동시에) 요약·증시 영향 3줄을 받고, 받은 섹션 요약으로 헤드라인 한 문장을 따로 받는다.
        실패한 섹션은 기사 목록 + 규칙 기반 영향 줄로 남는다.
        """
        if not self.settings()["summary"]:
            return None, None, None
        llm = self.llm()
        if llm is None:
            return None, None, None
        when = now.astimezone(KST)
        gate = asyncio.Semaphore(SUMMARY_CONCURRENCY)

        async def ask(prompt: str, max_tokens: int | None = None) -> str:
            async with gate:
                res = await llm.chat(
                    [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
            record_call(self.db, self.clock.now(), kind="newsroom", run_id=None, llm=llm, res=res)
            if res.finish_reason == "length" and extract_json(res.content or "") is None:
                raise TruncatedError("답이 출력 한도에서 잘렸어요")
            return res.content

        async def one(sec: dict[str, Any]) -> str | None:
            """섹션 하나 요약. 성공하면 None, 실패하면 오류 문구."""
            error = ""
            budget: int | None = None
            for _ in range(SECTION_TRIES):
                try:
                    _h, by_key, impact = parse_summary(
                        await ask(summary_prompt([sec], when, headline=False), budget),
                        [sec],
                        allow_empty=True,
                    )
                except Exception as e:  # 잘림·JSON 아님·일시 오류 → 한 번 더 (잘렸으면 출력 한도를 늘려서)
                    error = str(e)[:120]
                    if isinstance(e, TruncatedError) and llm.max_tokens > 0:
                        budget = llm.max_tokens * RETRY_TOKENS_FACTOR
                    continue
                sec["summary"] = by_key.get(sec["key"]) or None
                if sec["key"] in impact:
                    sec["impact"] = {"source": "ai", "lines": impact[sec["key"]]}
                return None
            log.warning("뉴스 룸 요약 실패(%s, %s): %s", llm.model, sec["key"], error)
            return error

        asked = [s for s in sections if s["items"]]
        errors = [e for e in await asyncio.gather(*(one(s) for s in asked)) if e is not None]
        if asked and len(errors) == len(asked):
            return None, None, f"요약 실패 — 기사 목록만 보여 줘요 ({errors[0]})"
        summaries = {s["key"]: s["summary"] for s in sections if s.get("summary")}
        headline = None
        if summaries:
            try:
                headline = parse_headline(await ask(headline_prompt(sections, summaries, when)))
            except Exception as e:  # 헤드라인이 없어도 섹션 요약은 그대로 쓴다
                log.warning("뉴스 룸 헤드라인 실패(%s): %s", llm.model, str(e)[:200])
        note = (
            f"일부 섹션 요약 실패 ({len(errors)}/{len(asked)}) — 그 섹션은 기사 목록과 규칙 기반 줄로 보여 줘요"
            if errors
            else None
        )
        return headline, llm.model, note


def _iso(t: datetime) -> str:
    """한국 시각 ISO (다음 갱신 시각과 같은 표기)."""
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(KST).isoformat()
