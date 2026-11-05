"""대화에서 자라는 기억 (D-67) — '최근 대화 5개'(D-47)를 바꾼 것.

투자자가 트레이딩 룸에서 꺼낸 주제(종목·업종·테마·운용 지시)를 주제별로 모은다.
- 여러 날(특히 다음 날·연달아) 되풀이해 물으면 중요도가 오르고 오래 남는다
- 한 번 묻고 만 주제는 그날만 들어가고 며칠 안에 빠진다 (하루 반감)
- 매니저(LLM)가 새 메시지를 볼 때마다 주제별로 올림/유지/내림/잊기를 정한다 ('잊어줘' → 다음 언급 전까지 0)
- 사용자는 메모 화면에서 주제를 늘 기억(고정)하거나 뺄 수 있다

중요도는 저장하지 않고 읽을 때 오늘 날짜로 다시 계산한다(score). 그래서 판단 세션이 없어도 날이 지나면 줄어든다.
LLM 호출은 '새 사용자 메시지가 있을 때 판단 세션 시작 전 1번'뿐이다(규칙 기반 모드는 정리하지 않는다).
주제를 지어내지 않게, 새 주제는 이번에 보낸 메시지 id 를 근거로 대야만 받는다.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent.llm import extract_json
from app.db.models import ChatMessage, MemoryTopic
from app.db.session import Database
from app.engine.settings_store import SettingsStore

log = logging.getLogger(__name__)

SETTINGS_KEY = "memory_settings"  # {"topics": bool} — 예전 키 use_recent_messages 는 쓰지 않는다
REVIEW_KEY = "memory_review"  # {"last_msg_id", "at", "fails"}
USER = "user"

THRESHOLD = 0.35  # 이 이상이면 판단·답장 기억에 들어간다
MAX_IN_PROMPT = 8
WINDOW_DAYS = 21  # 이 기간 안에 언급한 날만 센다 (드문드문 오래전 언급은 빠진다)
KEEP_DAYS = 30  # 주제마다 남겨 두는 언급 날짜 수
DROP_AFTER_DAYS = 60  # 이만큼 안 꺼낸 자동 주제는 지운다
LLM_STEP = 0.2  # 매니저 판단 한 칸만큼 중요도를 옮긴다
REVIEW_BATCH = 60  # 한 번에 정리하는 메시지 수
MSG_CHARS = 200
QUOTE_CHARS = 120
MAX_TOPICS_IN_REVIEW = 40
MAX_FAILS = 3  # 정리 응답이 이만큼 연달아 엉망이면 그 메시지들은 건너뛴다 (같은 호출을 계속 반복하지 않게)
ACTIONS = {"promote": 1, "keep": 0, "demote": -1}
PINS = ("", "pin", "mute")


def enabled(kv: SettingsStore) -> bool:
    return bool(kv.get(SETTINGS_KEY).get("topics", True))


def settings(kv: SettingsStore) -> dict[str, Any]:
    return {"topics": enabled(kv), "threshold": THRESHOLD, "max_in_prompt": MAX_IN_PROMPT}


def set_enabled(kv: SettingsStore, on: bool) -> dict[str, Any]:
    kv.set(SETTINGS_KEY, {"topics": bool(on)})
    return settings(kv)


# ====================================================================== 중요도
def _dates(raw: list[Any]) -> list[date]:
    out = []
    for x in raw or []:
        try:
            out.append(date.fromisoformat(str(x)))
        except ValueError:
            continue
    return sorted(set(out))


def streak(days: list[date]) -> int:
    """마지막 언급 날부터 거꾸로 연달아 언급한 날 수 (다음 날 또 물으면 2)."""
    if not days:
        return 0
    s = sorted(set(days))
    n = 1
    for a, b in zip(reversed(s[:-1]), reversed(s[1:]), strict=True):
        if (b - a).days != 1:
            break
        n += 1
    return n


def score(days: list[date], today: date, llm_level: int = 0, forgotten: bool = False) -> float:
    """0~1. 최근 21일 중 언급한 날이 많을수록(n) 높고 늦게 줄어든다.

    기본 = 0.4 + 0.6·(1 − e^(−(n−1)/2)) + 연달아 물은 날 보너스(하루당 0.05, 최대 0.15)
    마지막 언급 뒤로 반감기 1 + 1.5·(n−1)일(최대 10일)로 줄고, 매니저 판단(±0.2)을 더한다.
    예) 한 번: 그날 0.40 → 다음 날 0.20(빠짐) / 이틀 연달아: 다음 날 0.52 → 사흘 뒤 0.30 /
        나흘: 일주일 뒤에도 0.4 안팎
    """
    recent = sorted({d for d in days if 0 <= (today - d).days <= WINDOW_DAYS})
    if forgotten or not recent:
        return 0.0
    n = len(recent)
    base = 0.4 + 0.6 * (1 - math.exp(-(n - 1) / 2)) + min(0.15, 0.05 * (streak(recent) - 1))
    half = min(10.0, 1.0 + 1.5 * (n - 1))
    age = (today - recent[-1]).days
    v = base * 0.5 ** (age / half) + LLM_STEP * llm_level
    return round(max(0.0, min(1.0, v)), 3)


def _forgotten(t: MemoryTopic) -> bool:
    return t.forgot_at is not None and t.forgot_at >= t.last_seen


def topic_dict(t: MemoryTopic, today: date) -> dict[str, Any]:
    days = _dates(t.days)
    sc = score(days, today, t.llm_level, _forgotten(t))
    included = t.pin == "pin" or (t.pin != "mute" and sc >= THRESHOLD)
    return {
        "id": t.id,
        "key": t.key,
        "label": t.label,
        "symbol": t.symbol,
        "score": sc,
        "included": included,
        "pin": t.pin,
        "days": len([d for d in days if (today - d).days <= WINDOW_DAYS]),
        "streak": streak(days),
        "mentions": t.mentions,
        "last_day": days[-1].isoformat() if days else None,
        "quotes": list(t.quotes or [])[-3:],
        "llm_level": t.llm_level,
        "llm_reason": t.llm_reason or "",
        "forgotten": _forgotten(t),
    }


def all_topics(db: Database, today: date) -> list[dict[str, Any]]:
    """메모 화면용: 들어가는 것 먼저, 중요도 높은 순."""
    with db.session() as s:
        rows = [topic_dict(t, today) for t in s.scalars(select(MemoryTopic))]
    return sorted(rows, key=lambda x: (not x["included"], -x["score"], x["label"]))


def prompt_topics(db: Database, today: date) -> list[dict[str, Any]]:
    """판단·답장에 들어가는 주제 (고정 + 기준 이상, 최대 8개)."""
    return [t for t in all_topics(db, today) if t["included"]][:MAX_IN_PROMPT]


def render(topics: list[dict[str, Any]]) -> str:
    if not topics:
        return ""
    lines = []
    for t in topics:
        when = f"{t['days']}일 언급" + (
            f", 마지막 {t['last_day'][5:].replace('-', '/')}" if t["last_day"] else ""
        )
        tag = " · 늘 기억" if t["pin"] == "pin" else ""
        quote = t["quotes"][-1]["text"] if t["quotes"] else ""
        line = f"- {t['label']} [중요도 {t['score']:.2f} · {when}{tag}]"
        if quote:
            line += f": 최근 '{quote}'"
        lines.append(line)
    return (
        "투자자가 대화에서 되풀이한 관심사(자동 정리 — 중요도 순. 참고용 의견이며 리스크 규칙보다 우선하지 않음):\n"
        + "\n".join(lines)
    )


# ====================================================================== LLM 정리
REVIEW_SYSTEM = (
    "너는 투자자가 트레이딩 룸에서 한 말을 주제별로 정리하는 비서다. 반드시 JSON 객체 하나만 답한다. "
    "메시지에 없는 주제를 지어내지 마라."
)

REVIEW_PROMPT = """[새 메시지] (id · 날짜 · 내용)
{messages}

[지금 기억 중인 주제] (key · 이름 · 최근 21일 중 언급한 날 · 마지막 언급 · 지금 중요도 0~1)
{topics}

할 일
1. mentions: 새 메시지마다 다루는 투자 주제(종목·업종·테마·운용 지시)를 찾아라.
   - 이미 있는 주제면 그 key 를 그대로 쓴다. 새 주제면 key 를 비우고 label(짧은 이름)을 적고, 종목이면 symbol 에
     종목 코드(국장 6자리, 미장 티커)를 적는다(모르면 null).
   - message_ids 에는 그 주제를 실제로 말한 메시지 id 만 넣는다. 인사·맞장구·계좌 숫자 확인 같은 잡담은 넣지 않는다.
2. adjust: [지금 기억 중인 주제]와 이번에 새로 나온 주제마다, 투자자에게 앞으로도 중요한지 정한다.
   - promote: 여러 날 되풀이하거나 계속 유효한 지시·관심이라 더 오래 기억할 것
   - keep: 그대로 (날이 지나면 저절로 줄어든다)
   - demote: 한 번 묻고 끝났거나 이미 답이 나온 질문
   - forget: 투자자가 잊으라고·그만 보라고 했을 때
   reason 은 한국어 한 줄.

JSON 형식:
{{"mentions":[{{"key":"","label":"","symbol":null,"message_ids":[0]}}],"adjust":[{{"key":"","label":"","action":"keep","reason":""}}]}}
adjust 의 새 주제는 key 대신 mentions 와 같은 label 로 적는다."""


@dataclass
class Message:
    id: int
    ts: datetime
    day: date
    text: str


def normalize(label: str) -> str:
    return re.sub(r"[\s·,./()\[\]'\"!?~-]+", "", (label or "").lower())[:70]


def key_for(label: str, symbol: str | None) -> str:
    return f"sym:{symbol}" if symbol else f"t:{normalize(label)}"


def review_messages(msgs: list[Message], topics: list[dict[str, Any]]) -> list[dict[str, str]]:
    m = "\n".join(f"- #{x.id} · {x.day.strftime('%m/%d')} · {x.text[:MSG_CHARS]}" for x in msgs)
    t = (
        "\n".join(
            f"- {x['key']} · {x['label']} · {x['days']}일 · {x['last_day'] or '-'} · {x['score']:.2f}"
            for x in topics
        )
        or "- 없음"
    )
    return [
        {"role": "system", "content": REVIEW_SYSTEM},
        {"role": "user", "content": REVIEW_PROMPT.format(messages=m, topics=t)},
    ]


@dataclass
class Mention:
    key: str
    label: str
    symbol: str | None
    message_ids: list[int]


@dataclass
class Adjust:
    key: str
    action: str  # promote | keep | demote | forget
    reason: str


def parse_review(
    text: str,
    msg_ids: set[int],
    known: dict[str, str],
    name_of: Callable[[str], str | None] = lambda s: None,
) -> tuple[list[Mention], list[Adjust]] | None:
    """LLM 답 → (언급, 판단). JSON 이 아니면 None.

    - 언급: 이번 메시지 id 를 하나라도 근거로 대야 받는다(지어낸 주제 차단). 종목 코드는 그 시장 목록에 있어야
      종목 주제가 되고, 없으면 이름만 있는 주제로 둔다.
    - 판단: 이미 있는 주제이거나 이번에 새로 나온 주제만.
    `known`: 지금 있는 주제 key → label.
    """
    data = extract_json(text or "")
    if data is None:
        return None
    mentions: dict[str, Mention] = {}
    label_key: dict[str, str] = {normalize(v): k for k, v in known.items()}
    for it in data.get("mentions") or []:
        if not isinstance(it, dict):
            continue
        ids = sorted({n for n in (_to_int(i) for i in it.get("message_ids") or []) if n in msg_ids})
        if not ids:
            continue
        key = str(it.get("key") or "").strip()
        label = str(it.get("label") or "").strip()[:60]
        sym = str(it.get("symbol") or "").strip().upper()[:16] or None
        name = name_of(sym) if sym else None
        if sym and not name:
            sym = None  # 목록에 없는 코드는 믿지 않는다
        if key not in known:
            if sym:
                label = f"{name}({sym})" if name and name != sym else sym
            if not label:
                continue
            key = key_for(label, sym)
            key = label_key.get(normalize(label), key) if not sym else key
        else:
            label = known[key]
        m = mentions.get(key)
        if m is None:
            mentions[key] = Mention(key, label or known.get(key, key), sym, ids)
        else:
            m.message_ids = sorted(set(m.message_ids) | set(ids))
        label_key[normalize(mentions[key].label)] = key
    adjusts: list[Adjust] = []
    for it in data.get("adjust") or []:
        if not isinstance(it, dict):
            continue
        action = str(it.get("action") or "").strip().lower()
        if action not in ACTIONS and action != "forget":
            continue
        key = str(it.get("key") or "").strip()
        if key not in known and key not in mentions:
            key = label_key.get(normalize(str(it.get("label") or "")), "")
        if not key or (key not in known and key not in mentions):
            continue
        adjusts.append(Adjust(key, action, str(it.get("reason") or "").strip()[:200]))
    return list(mentions.values()), adjusts


def _to_int(v: Any) -> int | None:
    """메시지 id: 123 · "123" · "#123" (프롬프트에 #123 으로 보여 준다). 그 밖은 None."""
    if isinstance(v, bool):
        return None
    try:
        return int(str(v).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def apply_review(
    db: Database, now: datetime, msgs: list[Message], mentions: list[Mention], adjusts: list[Adjust]
) -> dict[str, int]:
    """언급을 주제 통계에 더하고(날짜·횟수·인용), 매니저 판단을 반영한다. 오래된 자동 주제는 지운다."""
    by_id = {m.id: m for m in msgs}
    out = {"mentioned": 0, "created": 0, "adjusted": 0, "dropped": 0}
    with db.session() as s:
        rows = {t.key: t for t in s.scalars(select(MemoryTopic))}
        for mt in mentions:
            hits = [by_id[i] for i in mt.message_ids if i in by_id]
            if not hits:
                continue
            t = rows.get(mt.key)
            if t is None:
                first = min(h.ts for h in hits)
                t = MemoryTopic(
                    key=mt.key,
                    label=mt.label[:80],
                    symbol=mt.symbol,
                    days=[],
                    mentions=0,
                    quotes=[],
                    first_seen=first,
                    last_seen=first,
                    llm_level=0,
                    llm_reason="",
                    pin="",
                )
                s.add(t)
                rows[mt.key] = t
                out["created"] += 1
            days = set(_dates(t.days)) | {h.day for h in hits}
            t.days = [d.isoformat() for d in sorted(days)[-KEEP_DAYS:]]
            seen = {int(q.get("id", 0)) for q in t.quotes or []}
            new = [h for h in hits if h.id not in seen]
            t.mentions = (t.mentions or 0) + len(new)
            t.quotes = (
                list(t.quotes or [])
                + [{"id": h.id, "at": h.ts.isoformat(), "text": h.text[:QUOTE_CHARS]} for h in new]
            )[-3:]
            last = max(h.ts for h in hits)
            if last > t.last_seen:
                t.last_seen = last
            if t.forgot_at is not None and last > t.forgot_at:
                t.forgot_at = None  # 잊으라고 한 뒤 다시 꺼냈다
            out["mentioned"] += 1
        batch_last = max((m.ts for m in msgs), default=now)
        for a in adjusts:
            t = rows.get(a.key)
            if t is None:
                continue
            if a.action == "forget":
                t.forgot_at = max(batch_last, t.last_seen)
                t.llm_level = 0
            else:
                t.llm_level = ACTIONS[a.action]
            t.llm_reason, t.llm_at = a.reason, now
            out["adjusted"] += 1
        cutoff = now - timedelta(days=DROP_AFTER_DAYS)
        for t in list(rows.values()):
            if t.id is not None and t.pin == "" and t.last_seen < cutoff:
                s.delete(t)
                out["dropped"] += 1
    return out


def pending(db: Database, kv: SettingsStore, now: datetime, tz: ZoneInfo) -> list[Message]:
    """아직 정리하지 않은 사용자 메시지 (처음이면 최근 21일 안의 최근 것만)."""
    last = int(kv.get(REVIEW_KEY).get("last_msg_id") or 0)
    with db.session() as s:
        q = select(ChatMessage).where(ChatMessage.role == USER, ChatMessage.id > last)
        if last == 0:
            q = q.where(ChatMessage.ts >= now - timedelta(days=WINDOW_DAYS))
            rows = list(s.scalars(q.order_by(ChatMessage.id.desc()).limit(REVIEW_BATCH)))[::-1]
        else:
            rows = list(s.scalars(q.order_by(ChatMessage.id).limit(REVIEW_BATCH)))
    return [
        Message(m.id, m.ts, m.ts.astimezone(tz).date(), (m.content or "").strip()) for m in rows if m.content
    ]


def mark_done(kv: SettingsStore, now: datetime, last_id: int) -> None:
    kv.set(REVIEW_KEY, {"last_msg_id": last_id, "at": now.isoformat(), "fails": 0})


def mark_failed(kv: SettingsStore, now: datetime, last_id: int) -> bool:
    """정리 실패를 센다. MAX_FAILS 번째면 그 메시지들을 건너뛰고 True."""
    st = kv.get(REVIEW_KEY)
    fails = int(st.get("fails") or 0) + 1
    if fails >= MAX_FAILS:
        mark_done(kv, now, last_id)
        return True
    kv.set(REVIEW_KEY, {**st, "fails": fails})
    return False


def review_state(kv: SettingsStore) -> dict[str, Any]:
    st = kv.get(REVIEW_KEY)
    return {
        "last_msg_id": int(st.get("last_msg_id") or 0),
        "at": st.get("at"),
        "fails": int(st.get("fails") or 0),
    }


def set_pin(db: Database, topic_id: int, pin: str) -> MemoryTopic | None:
    with db.session() as s:
        t = s.get(MemoryTopic, topic_id)
        if t is None:
            return None
        t.pin = pin
        s.flush()
        s.expunge(t)
        return t
