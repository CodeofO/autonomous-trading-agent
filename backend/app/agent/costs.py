"""LLM 토큰 사용량 기록과 상용 API 요금 환산 (D-41).

지금 모델(자체 서버)은 API 요금이 없지만, 같은 사용량을 상용 모델로 돌렸다면 얼마인지 미리 본다.
요금은 각 회사 공식 가격표를 PRICES_CHECKED 날짜에 확인한 값이다(아래 SOURCES). 가격이 바뀌면 이 표만 고친다.

- 토큰 수는 지금 모델의 토크나이저 기준이다. 같은 글도 회사·모델마다 토큰 수가 달라 실제 청구액과 차이가 난다.
- '캐시 적용'은 판단 세션 안에서 바로 전 호출의 입력이 다음 호출 앞부분으로 그대로 다시 들어가는 만큼을
  캐시 적중으로 본 추정이다. OpenAI·Google 은 자동 캐시(쓰기 요금 없음), Anthropic 은 cache_control 을
  써야 하고 새로 쓰는 부분은 5분 캐시 쓰기 요금(입력의 1.25배)을 낸다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.clock import KST
from app.core.market_calendar import MarketCalendar
from app.db.models import LlmUsage
from app.db.session import Database

log = logging.getLogger(__name__)

PRICES_CHECKED = "2026-11-04"
SOURCES = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "google": "https://aistudio.google.com/docs/pricing",
}
PROJECTION_DAYS = 30  # 하루 평균을 낼 최근 기간


@dataclass(frozen=True)
class Price:
    """USD / 100만 토큰."""

    input: float
    cached: float  # 캐시 적중 입력
    output: float
    cache_write: float | None = None  # Anthropic: 5분 캐시 쓰기
    since: date | None = None  # 이 날부터 적용 (예정된 가격 변경)


@dataclass(frozen=True)
class ModelPrice:
    id: str
    provider: str  # anthropic | openai | google
    name: str
    tier: str  # 최상위 | 주력 | 중간 | 경량
    prices: tuple[Price, ...]  # since 오름차순
    long_from: int | None = None  # 호출 1회 입력이 이 토큰 수를 넘으면 long 가격
    long: Price | None = None
    note: str = ""

    def at(self, day: date, input_tokens: int = 0) -> Price:
        if self.long and self.long_from and input_tokens > self.long_from:
            return self.long
        cur = self.prices[0]
        for p in self.prices:
            if p.since is None or p.since <= day:
                cur = p
        return cur


MODELS: tuple[ModelPrice, ...] = (
    ModelPrice("claude-fable-5-1", "anthropic", "Claude Fable 5.1", "최상위", (Price(10, 0.25, 50, 12.5),)),
    ModelPrice("claude-opus-5-5", "anthropic", "Claude Opus 5.5", "주력", (Price(4, 0.20, 20, 5),)),
    ModelPrice("claude-sonnet-5-5", "anthropic", "Claude Sonnet 5.5", "중간", (Price(2, 0.10, 10, 2.5),)),
    ModelPrice(
        "claude-haiku-5-5",
        "anthropic",
        "Claude Haiku 5.5",
        "경량",
        (Price(0.10, 0.01, 0.50, 0.125),),
        long_from=100_000,
        long=Price(0.50, 0.05, 2.50, 0.625),
        note="입력 10만 토큰 초과 호출은 5배 요금",
    ),
    ModelPrice(
        "gpt-6-astra",
        "openai",
        "GPT-6 Astra",
        "최상위",
        (Price(10, 1.00, 50),),
        long_from=272_000,
        long=Price(20, 2.00, 75),
        note="입력 27.2만 토큰 초과 호출은 장문 요금",
    ),
    ModelPrice("gpt-6.1-sol", "openai", "GPT-6.1 Sol", "주력", (Price(2, 0.10, 10),)),
    ModelPrice("gpt-6.1-terra", "openai", "GPT-6.1 Terra", "중간", (Price(1, 0.05, 5),)),
    ModelPrice("gpt-6-luna", "openai", "GPT-6 Luna", "경량", (Price(0.10, 0.01, 0.50),)),
    ModelPrice(
        "gemini-4-argon",
        "google",
        "Gemini 4 Argon",
        "주력",
        (Price(2, 0.10, 10), Price(4, 0.20, 20, since=date(2026, 12, 1))),
        note="2026-12-01 부터 2배로 인상 예정",
    ),
    ModelPrice(
        "gemini-3.1-pro-preview",
        "google",
        "Gemini 3.1 Pro (preview)",
        "중간",
        (Price(2, 0.20, 12),),
        long_from=200_000,
        long=Price(4, 0.40, 18),
        note="입력 20만 토큰 초과 호출은 장문 요금",
    ),
    ModelPrice(
        "gemini-3.8-flash",
        "google",
        "Gemini 3.8 Flash",
        "경량",
        (Price(0.75, 0.075, 3.75), Price(1.50, 0.15, 7.50, since=date(2027, 1, 1))),
        note="2027-01-01 부터 2배로 인상 예정",
    ),
    ModelPrice(
        "gemini-3.5-flash-lite", "google", "Gemini 3.5 Flash-Lite", "경량", (Price(0.30, 0.03, 2.50),)
    ),
)


# ====================================================================== 기록
def record_usage(
    db: Database,
    ts: datetime,
    kind: str,
    run_id: int | None,
    model: str,
    usage: dict[str, int],
    cached_estimate: int = 0,
) -> None:
    inp, out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    if inp == 0 and out == 0:
        return
    try:
        with db.session() as s:
            s.add(
                LlmUsage(
                    ts=ts,
                    kind=kind,
                    run_id=run_id,
                    model=model,
                    calls=1,
                    input_tokens=inp,
                    cached_tokens=max(0, min(cached_estimate, inp)),
                    output_tokens=out,
                    max_input_tokens=inp,
                    source="live",
                )
            )
    except Exception:  # 기록 실패가 판단·답장을 막지 않게
        log.exception("토큰 사용량 기록 실패")


# ====================================================================== 계산
USAGE_FIELDS = ("kind", "source", "input_tokens", "cached_tokens", "output_tokens", "max_input_tokens")


def row_cost(m: ModelPrice, r: LlmUsage | dict[str, Any], day: date) -> tuple[float, float]:
    """(캐시 없음, 캐시 적용) USD."""
    d: dict[str, Any] = r if isinstance(r, dict) else {k: getattr(r, k) for k in USAGE_FIELDS}

    def g(k: str, default: Any = 0) -> Any:
        v = d.get(k)
        return default if v is None else v

    inp, out, hit = int(g("input_tokens")), int(g("output_tokens")), int(g("cached_tokens"))
    p = m.at(day, int(g("max_input_tokens")))
    base = (inp * p.input + out * p.output) / 1e6
    miss = inp - hit
    # Anthropic 은 다음 호출이 이어지는 판단 세션 호출만 캐시에 쓴다 (단발 답장·예전 합계는 기본 입력 요금)
    write = (
        p.cache_write
        if (p.cache_write and g("kind", "") == "session" and g("source", "") == "live")
        else p.input
    )
    cached = (miss * write + hit * p.cached + out * p.output) / 1e6
    return base, cached


def _kst_day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=KST)).astimezone(KST).date()


def summary(
    db: Database,
    now: datetime,
    cal: MarketCalendar,
    months: int = 12,
    sessions_per_day: int | None = None,
) -> dict[str, Any]:
    """`sessions_per_day`: 지금 일정의 하루 판단 세션 수. 주면 '세션당 평균 × 하루 세션 수'로 예상한다
    (판단 간격을 바꾼 뒤에도 예전 간격의 사용량으로 부풀리지 않도록)."""
    today = now.astimezone(KST).date()
    first = date(today.year, today.month, 1)
    start = first
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    with db.session() as s:
        rows = list(
            s.scalars(
                select(LlmUsage).where(LlmUsage.ts >= datetime.combine(start, datetime.min.time(), KST))
            )
        )

    month_keys = []
    d = start
    while d <= first:
        month_keys.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    tok: dict[str, dict[str, int]] = {
        k: {"calls": 0, "input": 0, "cached": 0, "output": 0, "sessions": 0, "chats": 0, "backfill": 0}
        for k in month_keys
    }
    sessions: dict[str, set[int]] = defaultdict(set)
    cost: dict[str, dict[str, list[float]]] = {m.id: {k: [0.0, 0.0] for k in month_keys} for m in MODELS}
    daily: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        day = _kst_day(r.ts)
        k = day.strftime("%Y-%m")
        if k not in tok:
            continue
        t = tok[k]
        t["calls"] += r.calls
        t["input"] += r.input_tokens
        t["cached"] += r.cached_tokens
        t["output"] += r.output_tokens
        if r.kind == "chat":
            t["chats"] += 1
        elif r.run_id is not None:
            sessions[k].add(r.run_id)
        if r.source == "backfill":
            t["backfill"] += 1
        for m in MODELS:
            a, b = row_cost(m, r, day)
            cost[m.id][k][0] += a
            cost[m.id][k][1] += b
        if day > today - timedelta(days=PROJECTION_DAYS):
            daily[day].append({**{k: getattr(r, k) for k in USAGE_FIELDS}, "run_id": r.run_id})
    for k in month_keys:
        tok[k]["sessions"] = len(sessions[k])

    # 이번 달 예상 = 지금까지 쓴 것 + 하루 예상 × 남은 거래일
    #   하루 예상 = 세션당 평균 × 지금 일정의 하루 세션 수 + 답장의 하루 평균 (최근 30일 기준)
    #   (일정을 모르면 최근 사용한 날의 하루 평균)
    month_end = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    remaining = len(cal.trading_days(today + timedelta(days=1), month_end)) if today < month_end else 0
    active = len(daily)
    window = [(day, it) for day, items in daily.items() for it in items]
    n_sessions = len(
        {it["run_id"] for _, it in window if it["kind"] == "session" and it["run_id"] is not None}
    )
    by_schedule = sessions_per_day is not None and n_sessions > 0
    cur = first.strftime("%Y-%m")
    out_models = []
    for m in MODELS:
        sess, chat = [0.0, 0.0], [0.0, 0.0]
        for day, it in window:
            a, b = row_cost(m, it, day)
            bucket = sess if it["kind"] == "session" else chat
            bucket[0] += a
            bucket[1] += b
        if by_schedule:
            assert sessions_per_day is not None
            avg = [
                sessions_per_day * sess[i] / n_sessions + (chat[i] / active if active else 0.0)
                for i in (0, 1)
            ]
        else:
            avg = [(sess[i] + chat[i]) / active if active else 0.0 for i in (0, 1)]
        per_session = [sess[i] / n_sessions if n_sessions else 0.0 for i in (0, 1)]
        p = m.at(today)
        spent = cost[m.id][cur]
        out_models.append(
            {
                "id": m.id,
                "provider": m.provider,
                "name": m.name,
                "tier": m.tier,
                "note": m.note,
                "price": {
                    "input": p.input,
                    "cached": p.cached,
                    "output": p.output,
                    "cache_write": p.cache_write,
                    "long_from": m.long_from,
                },
                "months": {
                    k: {"nocache": round(v[0], 4), "cache": round(v[1], 4)} for k, v in cost[m.id].items()
                },
                "projection": {
                    "nocache": round(spent[0] + avg[0] * remaining, 4),
                    "cache": round(spent[1] + avg[1] * remaining, 4),
                },
                "daily_avg": {"nocache": round(avg[0], 4), "cache": round(avg[1], 4)},
                "per_session": {"nocache": round(per_session[0], 4), "cache": round(per_session[1], 4)},
            }
        )
    return {
        "checked": PRICES_CHECKED,
        "sources": SOURCES,
        "months": [{"month": k, **tok[k]} for k in month_keys],
        "models": out_models,
        "projection": {
            "month": cur,
            "remaining_trading_days": remaining,
            "basis_days": active,
            "basis_sessions": n_sessions,
            "basis_window_days": PROJECTION_DAYS,
            "method": "schedule" if by_schedule else "history",
            "sessions_per_day": sessions_per_day if by_schedule else None,
        },
    }
