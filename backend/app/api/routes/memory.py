"""메모 카드 API (D-52) · 대화에서 자라는 기억 (D-67)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent import memo, topics
from app.api.routes.common import C, U
from app.container import Container
from app.db.models import MemoryCard, MemoryTopic, WatchlistItem

router = APIRouter()


# ====================================================================== 메모 카드 (D-52)
class MemoCardCreateBody(BaseModel):
    kind: str = Field(max_length=16)
    symbol: str | None = Field(None, max_length=16)
    text: str | None = Field(None, max_length=2000)  # 500자 제한은 한국어 사유와 함께 따로 검사
    active: bool = True
    expires_on: date | None = None


class MemoCardPatchBody(BaseModel):
    kind: str | None = Field(None, max_length=16)
    text: str | None = Field(None, max_length=2000)
    active: bool | None = None
    expires_on: date | None = None


class MemorySettingsBody(BaseModel):
    topics: bool


class TopicPatchBody(BaseModel):
    pin: str = Field(max_length=8)  # '' 자동 · pin 늘 기억 · mute 빼기


def _market_today(c: Container) -> date:
    """만료 판단 기준 날짜 = 그 시장의 현지 날짜 (국장 한국 날짜, 미장 뉴욕 날짜)."""
    return c.cal.local_date(c.clock.now())


def _memo_kind(raw: str) -> str:
    kind = raw.strip().lower()
    if kind not in memo.KINDS:
        raise HTTPException(400, "알 수 없는 카드 종류예요 (watch·focus·avoid·note 중 하나)")
    return kind


def _memo_text(raw: str | None) -> str:
    text = (raw or "").strip()
    if len(text) > memo.MAX_TEXT:
        raise HTTPException(400, f"메모는 {memo.MAX_TEXT}자까지 적을 수 있어요")
    return text


def _memo_expiry(d: date | None, today: date) -> None:
    if d is not None and d < today:
        raise HTTPException(400, "만료일은 오늘이거나 그 뒤여야 해요")


async def _resolve_stock(c: Container, raw: str) -> tuple[str, str]:
    """그 시장의 종목인지 확인하고 (코드, 이름). 관심종목 추가와 같은 조회(토스 종목 목록·종목 정보·저장된 시세 이름)."""
    sym = raw.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", sym):
        raise HTTPException(400, "종목코드 형식이 아니에요")
    try:
        hit = (await c.toss.universe()).get(sym)
    except Exception:
        hit = None
    if hit:
        return sym, str(hit.get("name") or sym)
    try:
        info = (await c.toss.info([sym])).get(sym)
    except Exception:
        info = None
    markets = c.market.universe_markets if c.market else ()
    if info and (not info.get("market") or str(info.get("market")) in markets):
        return sym, str(info.get("name") or sym)
    if name := c.store.name_of(sym):
        return sym, name
    market = c.market.name if c.market else "국장"
    raise HTTPException(400, f"{market} 종목 목록에서 찾을 수 없는 종목이에요: {sym}")


def _memo_duplicate(s: Any, kind: str, symbol: str, today: date, exclude: int | None = None) -> None:
    """같은 종류·같은 종목의 켜진(만료 안 된) 카드가 이미 있으면 400."""
    q = select(MemoryCard).where(
        MemoryCard.kind == kind,
        MemoryCard.symbol == symbol,
        MemoryCard.active.is_(True),
        (MemoryCard.expires_on.is_(None)) | (MemoryCard.expires_on >= today),
    )
    if exclude is not None:
        q = q.where(MemoryCard.id != exclude)
    dup = s.scalars(q).first()
    if dup is not None:
        raise HTTPException(
            400, f"이미 켜져 있는 같은 카드가 있어요 ({memo.KIND_LABEL[kind]} · {dup.name or symbol})"
        )


def _ensure_watch(s: Any, symbol: str, name: str | None) -> None:
    """관심 종목 카드 → 기존 관심종목 목록(시세 갱신 대상)에도 넣는다. 카드를 지워도 목록에서는 빼지 않는다."""
    w = s.get(WatchlistItem, symbol)
    if w is None:
        s.add(WatchlistItem(symbol=symbol, name=name or ""))
    elif not w.active:
        w.active = True


def _card_title(card: dict[str, Any]) -> str:
    label = memo.KIND_LABEL.get(card["kind"], card["kind"])
    if card.get("symbol"):
        return f"{label} {card.get('name') or card['symbol']}({card['symbol']})"
    text = str(card.get("text") or "")
    return f"{label} '{text[:20]}{'…' if len(text) > 20 else ''}'"


@router.get("/memory/cards")
def memo_cards(c: C, _: U) -> dict[str, Any]:
    """메모 카드 전체(꺼진·만료된 것 포함)와 기억 설정. today = 만료 판단 기준 날짜(시장 현지)."""
    today = _market_today(c)
    return {
        "cards": [memo.card_dict(x, today) for x in memo.all_cards(c.db)],
        "settings": topics.settings(c.kv),
        "today": today.isoformat(),
    }


@router.post("/memory/cards")
async def create_memo_card(body: MemoCardCreateBody, c: C, _: U) -> dict[str, Any]:
    today = _market_today(c)
    kind = _memo_kind(body.kind)
    text = _memo_text(body.text)
    _memo_expiry(body.expires_on, today)
    symbol: str | None = None
    name: str | None = None
    if kind in memo.STOCK_KINDS:
        if not (body.symbol or "").strip():
            raise HTTPException(400, "종목을 골라 주세요")
        symbol, name = await _resolve_stock(c, body.symbol or "")
    elif not text:
        raise HTTPException(400, "운용 지시 내용을 적어 주세요")
    now = c.clock.now()
    with c.db.session() as s:
        if body.active and symbol is not None:
            _memo_duplicate(s, kind, symbol, today)
        card = MemoryCard(
            kind=kind,
            symbol=symbol,
            name=name,
            text=text,
            active=body.active,
            expires_on=body.expires_on,
            created_at=now,
            updated_at=now,
        )
        s.add(card)
        if kind == "watch" and symbol is not None:
            _ensure_watch(s, symbol, name)
        s.flush()
        out = memo.card_dict(card, today)
    c.hub.audit("CONFIG", f"메모 카드 추가: {_card_title(out)}", symbol, card=out)
    return out


@router.patch("/memory/cards/{card_id}")
def update_memo_card(card_id: int, body: MemoCardPatchBody, c: C, _: U) -> dict[str, Any]:
    today = _market_today(c)
    fields = body.model_fields_set
    with c.db.session() as s:
        card = s.get(MemoryCard, card_id)
        if card is None:
            raise HTTPException(404, "메모 카드를 찾을 수 없어요")
        kind = card.kind
        if "kind" in fields and body.kind is not None:
            kind = _memo_kind(body.kind)
            if (kind in memo.STOCK_KINDS) != (card.kind in memo.STOCK_KINDS):
                raise HTTPException(400, "종목 카드와 운용 지시는 서로 바꿀 수 없어요")
        text = _memo_text(body.text) if "text" in fields else card.text
        if kind == "note" and not text:
            raise HTTPException(400, "운용 지시는 비울 수 없어요")
        active = body.active if "active" in fields and body.active is not None else card.active
        expires_on = card.expires_on
        if "expires_on" in fields:
            if body.expires_on != card.expires_on:
                _memo_expiry(body.expires_on, today)
            expires_on = body.expires_on
        live = active and (expires_on is None or expires_on >= today)
        if live and card.symbol:
            _memo_duplicate(s, kind, card.symbol, today, exclude=card.id)
        if live and kind == "watch" and card.symbol and (kind != card.kind or active != card.active):
            _ensure_watch(s, card.symbol, card.name)
        before = memo.card_dict(card, today)
        card.kind, card.text, card.active, card.expires_on = kind, text, active, expires_on
        card.updated_at = c.clock.now()
        s.flush()
        out = memo.card_dict(card, today)
    changed = sorted(k for k in ("kind", "text", "active", "expires_on") if before[k] != out[k])
    if changed:
        c.hub.audit(
            "CONFIG", f"메모 카드 변경: {_card_title(out)} ({', '.join(changed)})", out["symbol"], card=out
        )
    return out


@router.delete("/memory/cards/{card_id}", status_code=204)
def delete_memo_card(card_id: int, c: C, _: U) -> Response:
    """카드만 지운다. 관심 종목 카드로 들어간 관심종목 목록 항목은 그대로 둔다(따로 관리)."""
    today = _market_today(c)
    with c.db.session() as s:
        card = s.get(MemoryCard, card_id)
        if card is None:
            raise HTTPException(404, "메모 카드를 찾을 수 없어요")
        out = memo.card_dict(card, today)
        s.delete(card)
    c.hub.audit("CONFIG", f"메모 카드 삭제: {_card_title(out)}", out["symbol"], card=out)
    return Response(status_code=204)


@router.put("/memory/settings")
def put_memory_settings(body: MemorySettingsBody, c: C, _: U) -> dict[str, Any]:
    """대화에서 기억 만들기(D-67)를 켜고 끈다. 끄면 정리도 하지 않고 주제도 기억에 넣지 않는다(주제 기록은 남는다)."""
    prev = topics.enabled(c.kv)
    out = topics.set_enabled(c.kv, body.topics)
    if prev != body.topics:
        state = "켬" if body.topics else "끔"
        c.hub.audit("CONFIG", f"대화에서 기억하기 {state}", None, topics=body.topics)
    return out


# ====================================================================== 대화에서 자라는 기억 (D-67)
def _topics_view(c: Container) -> dict[str, Any]:
    today = _market_today(c)
    return {
        "topics": topics.all_topics(c.db, today),
        "settings": topics.settings(c.kv),
        "review": topics.review_state(c.kv),
        "llm": c.runner.llm is not None,
        "today": today.isoformat(),
    }


@router.get("/memory/topics")
def memory_topics(c: C, _: U) -> dict[str, Any]:
    """대화에서 모은 주제 전체(빠진 것 포함) — 중요도·언급한 날·매니저 판단과 이유."""
    return _topics_view(c)


@router.patch("/memory/topics/{topic_id}")
def patch_memory_topic(topic_id: int, body: TopicPatchBody, c: C, _: U) -> dict[str, Any]:
    """늘 기억(pin) · 빼기(mute) · 자동('') 중 하나로."""
    pin = body.pin.strip().lower()
    if pin not in topics.PINS:
        raise HTTPException(400, "pin 은 '', 'pin', 'mute' 중 하나예요")
    t = topics.set_pin(c.db, topic_id, pin)
    if t is None:
        raise HTTPException(404, "주제를 찾을 수 없어요")
    out = topics.topic_dict(t, _market_today(c))
    state = {"": "자동", "pin": "늘 기억", "mute": "빼기"}[pin]
    c.hub.audit("CONFIG", f"기억 주제 {state}: {out['label']}", out["symbol"], topic=out["key"], pin=pin)
    return out


@router.delete("/memory/topics/{topic_id}", status_code=204)
def delete_memory_topic(topic_id: int, c: C, _: U) -> Response:
    """주제 기록을 지운다 (다시 꺼내면 처음부터 다시 센다)."""
    with c.db.session() as s:
        t = s.get(MemoryTopic, topic_id)
        if t is None:
            raise HTTPException(404, "주제를 찾을 수 없어요")
        label = t.label
        s.delete(t)
    c.hub.audit("CONFIG", f"기억 주제 삭제: {label}", None)
    return Response(status_code=204)


@router.post("/memory/topics/review")
async def review_memory_topics(c: C, _: U) -> dict[str, Any]:
    """아직 정리하지 않은 내 메시지를 지금 정리한다 (LLM 1번). 보통은 판단 세션 시작 전에 저절로 한다."""
    if not topics.enabled(c.kv):
        raise HTTPException(400, "대화에서 기억하기가 꺼져 있어요")
    if c.runner.llm is None:
        raise HTTPException(400, "LLM 이 설정되지 않아 정리할 수 없어요")
    result = await c.runner.review_topics()
    return {**_topics_view(c), "result": result}
