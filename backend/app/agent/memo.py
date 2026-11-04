"""메모 카드 — 사용자가 직접 관리하는 매니저의 기억 (D-52).

예전에는 트레이딩 룸에 보낸 사용자 메시지 최근 5개가 날짜와 관계없이 '투자자 메모'로 들어가, 며칠 전 한 말이 계속
판단에 끼어들었다. 이제는 사용자가 카드(관심·주목·피할 종목, 운용 지시)를 만들고 켜고 끄고 만료일을 정한다.
켜져 있고 만료되지 않은 카드만 판단·답장 프롬프트에 들어간다. 최근 대화 메모는 설정(kv `memory_settings`)으로 끌 수 있다.
카드는 시장 스택마다 따로(각 DB) 있다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, select

from app.db.models import MemoryCard
from app.db.session import Database
from app.engine.settings_store import SettingsStore

KINDS: tuple[str, ...] = ("watch", "focus", "avoid", "note")  # 정렬 순서이기도 하다
STOCK_KINDS: frozenset[str] = frozenset({"watch", "focus", "avoid"})
KIND_LABEL = {"watch": "관심 종목", "focus": "주목할 종목", "avoid": "피할 종목", "note": "운용 지시"}
MAX_TEXT = 500
SETTINGS_KEY = "memory_settings"
RECENT_MESSAGES = 5  # 최근 대화 메모 개수 (AgentRunner.MEMORY_MEMOS 와 같다)

# 프롬프트 섹션 제목 — 피할 종목은 프롬프트로 부드럽게 막는다(리스크 엔진이 최종 방어선, 리스크 규칙은 그대로)
PROMPT_SECTIONS = {
    "watch": "관심 종목 (계속 지켜볼 것)",
    "focus": "주목할 종목 (적극적으로 매수 검토)",
    "avoid": "피할 종목(매수하지 말 것 — BUY 금지, 이미 보유 중이면 HOLD 또는 SELL 만)",
    "note": "운용 지시",
}


def memory_settings(kv: SettingsStore) -> dict[str, Any]:
    """기억 설정. use_recent_messages 기본 true (예전 동작 그대로 — 화면에서 끌 수 있다)."""
    raw = kv.get(SETTINGS_KEY)
    return {
        "use_recent_messages": bool(raw.get("use_recent_messages", True)),
        "recent_messages": RECENT_MESSAGES,
    }


def set_memory_settings(kv: SettingsStore, use_recent_messages: bool) -> dict[str, Any]:
    kv.set(SETTINGS_KEY, {"use_recent_messages": use_recent_messages})
    return memory_settings(kv)


def kind_order(kind: str) -> int:
    return KINDS.index(kind) if kind in KINDS else len(KINDS)


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    return (
        (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    )  # 만든 직후와 다시 읽을 때 같은 표기


def is_expired(card: MemoryCard, today: date) -> bool:
    """만료일 다음 날부터 만료 (그날까지는 유효)."""
    return card.expires_on is not None and card.expires_on < today


def card_dict(card: MemoryCard, today: date) -> dict[str, Any]:
    return {
        "id": card.id,
        "kind": card.kind,
        "symbol": card.symbol,
        "name": card.name,
        "text": card.text or "",
        "active": card.active,
        "expires_on": card.expires_on.isoformat() if card.expires_on else None,
        "expired": is_expired(card, today),
        "created_at": _iso(card.created_at),
        "updated_at": _iso(card.updated_at),
    }


def sort_cards(cards: list[MemoryCard]) -> list[MemoryCard]:
    """종류(관심 → 주목 → 피할 → 운용 지시) 다음 새 카드 먼저."""
    return sorted(
        cards,
        key=lambda c: (kind_order(c.kind), -(c.created_at.timestamp() if c.created_at else 0), -c.id),
    )


def all_cards(db: Database) -> list[MemoryCard]:
    with db.session() as s:
        rows = list(s.scalars(select(MemoryCard)))
    return sort_cards(rows)


def live_cards(db: Database, today: date) -> list[dict[str, Any]]:
    """지금 기억에 들어가는 카드 (켜짐 · 만료 안 됨): {kind, symbol, name, text}."""
    with db.session() as s:
        rows = list(
            s.scalars(
                select(MemoryCard).where(
                    MemoryCard.active.is_(True),
                    or_(MemoryCard.expires_on.is_(None), MemoryCard.expires_on >= today),
                )
            )
        )
    return [
        {"kind": c.kind, "symbol": c.symbol, "name": c.name, "text": c.text or ""} for c in sort_cards(rows)
    ]


def render_cards(cards: list[dict[str, Any]]) -> str:
    """프롬프트용: 종류별 섹션. 카드가 없으면 빈 문자열."""
    blocks: list[str] = []
    for kind in KINDS:
        rows = [c for c in cards if c["kind"] == kind]
        if not rows:
            continue
        lines = []
        for c in rows:
            if kind == "note":
                lines.append(f"- {c['text']}")
                continue
            who = f"{c['name']}({c['symbol']})" if c.get("name") else str(c["symbol"])
            lines.append(f"- {who}" + (f": {c['text']}" if c.get("text") else ""))
        blocks.append(f"{PROMPT_SECTIONS[kind]}:\n" + "\n".join(lines))
    if not blocks:
        return ""
    return "투자자 메모 카드(투자자가 직접 정한 지시 — 따르되 리스크 규칙보다 우선하지 않음):\n" + "\n".join(
        blocks
    )
