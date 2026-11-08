"""LLM 요금 장부 (D-41 → D-66): 호출마다 어느 모델이 토큰을 얼마나 썼고 실제 요금이 얼마인지.

요금은 호출 1회마다 이 순서로 정한다 (cost_source):
1. reported  — 제공사가 응답에 적어 준 요금. OpenRouter 는 usage.cost(USD, 캐시·추론 반영)를 늘 준다
               (https://openrouter.ai/docs/cookbook/administration/usage-accounting)
2. free      — 자체 서버(provider=local, 무료 묶음 LLM_TEST_*): API 요금 없음
3. priced    — OpenAI·Google 직접 연결: 응답의 토큰(캐시 적중·추론 포함)을 공식 단가표(아래 MODELS)로 계산
4. unpriced  — 단가를 모르는 모델 (화면에 '단가 모름'으로 따로 센다 — 0 원으로 치지 않는다)
5. estimated — D-66 이전 기록: 캐시 적중을 알 수 없어 예전 추정(바로 전 호출 입력)으로 계산

국장·미장·전략 실험실·뉴스 룸의 모든 호출을 국장 DB 한 곳(장부)에 모은다 (market·strategy 로 구분).
단가는 각 회사 공식 가격표를 PRICES_CHECKED 날짜에 확인한 값이다(SOURCES). 가격이 바뀌면 이 표만 고친다.
"""

from __future__ import annotations

import copy
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from sqlalchemy import delete, func, select, update

from app.agent import openrouter
from app.core.clock import KST
from app.db.models import LlmUsage
from app.db.session import Database

if TYPE_CHECKING:
    from app.agent.llm import ChatResult, LLMClient

log = logging.getLogger(__name__)

PRICES_CHECKED = "2026-11-04"
SOURCES = {
    "openai": "https://developers.openai.com/api/docs/pricing",
    "google": "https://aistudio.google.com/docs/pricing",
    "openrouter": "https://openrouter.ai/docs/cookbook/administration/usage-accounting",
    "qwen": "https://docs.qwencloud.com/developer-guides/getting-started/model-prices",
}


@dataclass(frozen=True)
class Price:
    """USD / 100만 토큰."""

    input: float
    cached: float  # 캐시 적중 입력
    output: float  # 추론 토큰도 출력 요금
    since: date | None = None  # 이 날부터 적용 (예정된 가격 변경)


@dataclass(frozen=True)
class ModelPrice:
    id: str
    provider: str  # openai | google
    name: str
    tier: str  # 최상위 | 주력 | 중간 | 경량
    prices: tuple[Price, ...]  # since 오름차순
    long_from: int | None = None  # 호출 1회 입력이 이 토큰 수를 넘으면 long 가격
    long: Price | None = None
    note: str = ""
    # 입력 구간이 셋 이상인 모델 (Qwen 등): (이 입력 토큰 수를 넘으면, 그 가격) 오름차순 — long 보다 먼저 본다
    tiers: tuple[tuple[int, Price], ...] = ()

    def at(self, day: date, input_tokens: int = 0) -> Price:
        for from_, p in reversed(self.tiers):
            if input_tokens > from_:
                return p
        if self.long and self.long_from and input_tokens > self.long_from:
            return self.long
        cur = self.prices[0]
        for p in self.prices:
            if p.since is None or p.since <= day:
                cur = p
        return cur


MODELS: tuple[ModelPrice, ...] = (
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
    # Qwen Cloud (D-73): 국제(싱가포르) 가격, 생각 모드 출력도 같은 값. 캐시 적중은 자동(암묵) 캐시 단가 —
    # 모델 페이지(https://www.qwencloud.com/models/<id>)의 'Input (Implicit Cache)' 값 (2026-11-09 확인).
    # 3.8 계열은 입력의 20% 가 아니다(공식 context-cache 문서). 표에 없는 3.7 구간은 문서 규칙(입력의 20%)으로 둔다.
    # https://docs.qwencloud.com/developer-guides/getting-started/model-prices
    # https://docs.qwencloud.com/developer-guides/text-generation/context-cache
    ModelPrice("qwen3.8-max", "qwen", "Qwen3.8 Max", "최상위", (Price(2, 0.25, 6),)),
    ModelPrice("qwen3.7-max", "qwen", "Qwen3.7 Max", "최상위", (Price(2.5, 0.5, 7.5),)),
    ModelPrice(
        "qwen3.7-plus",
        "qwen",
        "Qwen3.7 Plus",
        "주력",
        # 한시 20% 할인가(정가 0.4 / 0.08 / 1.6, 장문 1.2 / 4.8) — 할인이 끝나면 정가로 되돌린다
        (Price(0.32, 0.064, 1.28),),
        long_from=256_000,
        long=Price(0.96, 0.192, 3.84),
        note="한시 20% 할인가 · 입력 25.6만 토큰 초과 호출은 장문 요금",
    ),
    ModelPrice("qwen3.8-flash", "qwen", "Qwen3.8 Flash", "경량", (Price(0.15, 0.016, 0.47),)),
    ModelPrice(
        "qwen3.7-flash",
        "qwen",
        "Qwen3.7 Flash",
        "경량",
        (Price(0.03, 0.006, 0.13),),
        tiers=((32_000, Price(0.10, 0.02, 0.40)), (256_000, Price(0.20, 0.04, 0.80))),
        note="입력 3.2만·25.6만 토큰 초과 호출은 구간 요금",
    ),
)
CATALOG = {m.id: m for m in MODELS}
# 추론 토큰: OpenAI 는 출력(output/completion_tokens)에 들어 있다 (https://developers.openai.com/api/docs/guides/reasoning).
# Google 의 OpenAI 호환 주소는 completion_tokens 에 생각(thinking) 토큰이 들어가는지 문서에 없다. 기본 API 는
# totalTokenCount = 입력 + 도구 입력 + 생각 + 출력 이고 생각은 출력 요금이다(https://ai.google.dev/api/generate-content,
# …/docs/pricing). 그래서 total − 입력 − 출력 이 남으면 그만큼을 드러나지 않은 생각 토큰으로 보고 출력 요금을 매긴다.
HIDDEN_REASONING: frozenset[str] = frozenset({"google"})
# OpenAI 캐시 쓰기 (GPT-5.6 이후 모델): 입력 단가 대신 30분 1.25배·12시간 2.5배
# (https://developers.openai.com/api/docs/guides/prompt-caching). input_tokens 에 캐시 읽기·쓰기가 들어 있다
CACHE_WRITE_X: dict[str, tuple[float, float]] = {"openai": (1.25, 2.5)}
# 응답의 service_tier 별 요금: flex 는 Batch 요금(50%), fast·priority 등은 별도 표라 단가 모름으로 둔다
# (https://developers.openai.com/api/docs/guides/flex-processing, …/fast-mode)
TIER_X: dict[str, float | None] = {
    "": 1.0,
    "auto": 1.0,
    "default": 1.0,
    "flex": 0.5,
    "batch": 0.5,
    "priority": None,
    "fast": None,
    "ultrafast": None,
    "scale": None,
}


def catalog_price(model: str) -> ModelPrice | None:
    """단가표 모델 찾기: 그대로 → 'openai/…' 같은 앞부분 뗀 것 → '-2026-10-01' 같은 날짜 꼬리 뗀 것."""
    m = (model or "").strip()
    if m in CATALOG:
        return CATALOG[m]
    tail = m.rsplit("/", 1)[-1]
    if tail in CATALOG:
        return CATALOG[tail]
    for mid, mp in CATALOG.items():
        if tail.startswith(mid + "-") and tail[len(mid) + 1 : len(mid) + 2].isdigit():
            return mp
    return None


# ====================================================================== 요금 매기기
@dataclass(frozen=True)
class Charge:
    cost: float | None  # USD (unpriced 면 None)
    source: str  # reported | priced | free | unpriced | estimated


def price_tokens(
    mp: ModelPrice,
    day: date,
    *,
    input: int,
    cache_read: int,
    output: int,
    hidden: int = 0,
    cache_write: int = 0,
    cache_write_12h: int = 0,
) -> float:
    """공식 단가로 1회 요금.

    입력 = 캐시 읽기(캐시 단가) + 캐시 쓰기(OpenAI: 30분 1.25배·12시간 2.5배) + 나머지(기본 단가),
    출력(추론 포함) + hidden(출력에 안 잡힌 생각 토큰)은 출력 단가. 장문 구간은 호출 1회 입력으로 정한다.
    """
    p = mp.at(day, input)
    hit = max(0, min(cache_read, input))
    x30, x12 = CACHE_WRITE_X.get(mp.provider, (1.0, 1.0))
    w12 = max(0, min(cache_write_12h, input - hit))
    w30 = max(0, min(cache_write - cache_write_12h, input - hit - w12)) if cache_write else 0
    rest = input - hit - w30 - w12
    out = output + max(0, hidden)
    return (rest * p.input + hit * p.cached + (w30 * x30 + w12 * x12) * p.input + out * p.output) / 1e6


def _openrouter_price(model: str, input: int, output: int) -> float | None:
    """OpenRouter 가 요금을 적어 주지 않았을 때만: 목록 API 단가(캐시 할인 없이)로."""
    m = openrouter.CACHE.get(model)
    if m is None or m.input is None or m.output is None:
        return None
    return (input * m.input + output * m.output) / 1e6


def hidden_reasoning(provider: str, usage: dict[str, int]) -> int:
    """출력에 잡히지 않은 생각 토큰 (Google OpenAI 호환: total − 입력 − 출력 이 남는 만큼). 그 밖은 0."""
    if provider not in HIDDEN_REASONING or "total_tokens" not in usage:
        return 0
    rest = int(usage.get("total_tokens") or 0) - int(usage.get("prompt_tokens") or 0)
    return max(0, rest - int(usage.get("completion_tokens") or 0))


def charge(
    provider: str,
    model: str,
    usage: dict[str, int],
    reported: float | None,
    day: date,
    service_tier: str = "",
) -> Charge:
    if reported is not None:
        return Charge(max(0.0, float(reported)), "reported")
    if provider == "local":
        return Charge(0.0, "free")
    inp, out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    if provider == "openrouter":
        v = _openrouter_price(model, inp, out)
        return Charge(v, "priced") if v is not None else Charge(None, "unpriced")
    mp = catalog_price(model)
    tier = TIER_X.get((service_tier or "").lower(), None)
    if mp is None or tier is None:
        return Charge(None, "unpriced")
    cost = price_tokens(
        mp,
        day,
        input=inp,
        cache_read=int(usage.get("cached_tokens") or 0),
        output=out,
        hidden=hidden_reasoning(mp.provider, usage),
        cache_write=int(usage.get("cache_write_tokens") or 0),
        cache_write_12h=int(usage.get("cache_write_12h_tokens") or 0),
    )
    return Charge(cost * tier, "priced")


def _kst_day(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=KST)).astimezone(KST).date()


def public_model(model: str) -> str:
    """화면에 내보내는 모델 이름: 자체 서버 주소('…@호스트')는 떼어 낸다."""
    return (model or "").split("@", 1)[0]


def _price_dict(p: Price) -> dict[str, float]:
    return {"input": p.input, "cached": p.cached, "output": p.output}


def price_list(today: date) -> list[dict[str, Any]]:
    """화면의 '주요 모델 단가' 표 (USD / 100만 토큰): 오늘 적용 단가, 장문 구간, 예정된 변경.

    cache_write: OpenAI 30분 캐시 쓰기 단가(입력의 1.25배) — 캐시 쓰기 요금이 없는 제공사는 None.
    """
    out = []
    for m in MODELS:
        p = m.at(today)
        nxt = next((x for x in m.prices if x.since is not None and x.since > today), None)
        x30 = CACHE_WRITE_X.get(m.provider, (None, None))[0]
        out.append(
            {
                "id": m.id,
                "provider": m.provider,
                "name": m.name,
                "tier": m.tier,
                **_price_dict(p),
                "cache_write": round(p.input * x30, 6) if x30 else None,
                "long_from": m.long_from,
                "long": _price_dict(m.long) if m.long else None,
                "tiers": [{"from": f, **_price_dict(tp)} for f, tp in m.tiers],
                "next": {"since": nxt.since.isoformat(), **_price_dict(nxt)} if nxt and nxt.since else None,
                "note": m.note,
            }
        )
    return out


# ====================================================================== 기록
def record_call(
    db: Database,
    ts: datetime,
    *,
    kind: str,
    run_id: int | None,
    llm: LLMClient,
    res: ChatResult,
    market: str = "",
    strategy: str = "",
    cached_estimate: int = 0,
) -> Charge | None:
    """호출 1회를 장부에 남기고 요금을 돌려준다. 기록 실패가 판단·답장을 막지 않게 예외는 삼킨다."""
    usage = res.usage or {}
    inp, out = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    if inp == 0 and out == 0 and res.cost is None:
        return None
    provider = llm.provider
    try:
        meta = res.meta or {}
        ch = charge(provider, llm.model, usage, res.cost, _kst_day(ts), meta.get("service_tier", ""))
        with db.session() as s:
            s.add(
                LlmUsage(
                    ts=ts,
                    kind=kind,
                    run_id=run_id,
                    model=llm.model,
                    calls=1,
                    input_tokens=inp,
                    cached_tokens=max(0, min(cached_estimate, inp)),
                    output_tokens=out,
                    max_input_tokens=inp,
                    source="live",
                    market=market,
                    strategy=strategy,
                    profile=getattr(llm, "profile", "") or "",
                    provider=provider,
                    served_model=meta.get("model", "")[:128],
                    upstream=meta.get("provider", "")[:64],
                    response_id=meta.get("id", "")[:128],
                    cache_read_tokens=int(usage["cached_tokens"]) if "cached_tokens" in usage else None,
                    cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
                    reasoning_tokens=max(
                        int(usage.get("reasoning_tokens") or 0), hidden_reasoning(provider, usage)
                    ),
                    cost_usd=ch.cost,
                    cost_source=ch.source,
                )
            )
        return ch
    except Exception:
        log.exception("LLM 요금 장부 기록 실패")
        return None


def _fill_legacy(r: LlmUsage) -> None:
    """D-66 이전 기록: 모델 이름으로 제공사를 짐작하고, 캐시 적중은 예전 추정치로 요금을 매긴다."""
    model = r.model or ""
    if "@" in model:  # 자체 서버 (모델 이름 뒤에 서버 주소를 붙여 둔 것)
        r.provider, r.profile, r.cost_usd, r.cost_source = "local", "free", 0.0, "free"
        return
    mp = catalog_price(model)
    if mp is not None:
        r.provider, r.profile = mp.provider, "paid"
        r.cost_usd = price_tokens(
            mp,
            _kst_day(r.ts),
            input=r.input_tokens,
            cache_read=r.cached_tokens,
            output=r.output_tokens,
        )
        r.cost_source = "estimated"
        return
    if "/" in model:
        r.provider, r.profile = "openrouter", "paid"
        v = _openrouter_price(model, r.input_tokens, r.output_tokens)
        r.cost_usd, r.cost_source = (v, "estimated") if v is not None else (None, "unpriced")
        return
    r.cost_usd, r.cost_source = None, "unpriced"


def backfill(db: Database, market: str = "") -> int:
    """요금이 비어 있는 예전 기록에 요금을 매긴다 (부팅 때). 고친 행 수.

    market 을 주면 그 DB 에서 바로 쌓인(옮겨 온 것이 아닌) 예전 판단·답장 기록에 시장을 적는다.
    뉴스 룸 기록은 공용('')으로 둔다. 새 기록은 처음부터 시장이 있어 다시 바뀌지 않는다.
    """
    with db.session() as s:
        rows = list(s.scalars(select(LlmUsage).where(LlmUsage.cost_source == "")))
        for r in rows:
            _fill_legacy(r)
        if market:
            s.execute(
                update(LlmUsage)
                .where(LlmUsage.market == "", LlmUsage.origin.is_(None), LlmUsage.kind != "newsroom")
                .values(market=market)
            )
    if rows:
        log.info("LLM 요금 장부: 예전 기록 %d건에 요금을 매김", len(rows))
    return len(rows)


COPY_FIELDS = (
    "ts",
    "kind",
    "run_id",
    "model",
    "calls",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "max_input_tokens",
    "source",
    "profile",
    "provider",
    "served_model",
    "upstream",
    "response_id",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "cost_usd",
    "cost_source",
)


def move_usage(src: Database, ledger: Database, market: str, strategy: str = "") -> int:
    """미장·실험실 DB 에 쌓인 기록을 장부(국장 DB)로 옮긴다. origin 으로 한 번만 옮기고, 옮긴 뒤 원래 행은 지운다.

    origin = '시장:전략:원래 id:시각(초)' — SQLite 는 행을 지우면 id 를 다시 쓸 수 있어 시각까지 붙인다.
    """
    if src is ledger:
        return 0
    prefix = f"{market}:{strategy}:"
    with src.session() as s:
        rows = list(s.scalars(select(LlmUsage).order_by(LlmUsage.id)))
        if not rows:
            return 0
        data = [
            {f: getattr(r, f) for f in COPY_FIELDS} | {"src_id": f"{r.id}:{int(r.ts.timestamp())}"}
            for r in rows
        ]
        ids = [r.id for r in rows]
    with ledger.session() as s:
        done = set(s.scalars(select(LlmUsage.origin).where(LlmUsage.origin.like(prefix + "%"))))
        moved = 0
        for d in data:
            origin = f"{prefix}{d.pop('src_id')}"
            if origin in done:
                continue
            row = LlmUsage(**d, market=market, strategy=strategy, origin=origin)
            if not row.cost_source:
                _fill_legacy(row)
            s.add(row)
            moved += 1
    with src.session() as s:  # 장부에 들어간 것을 확인한 뒤에만 지운다
        s.execute(delete(LlmUsage).where(LlmUsage.id.in_(ids)))
    if moved:
        log.info("LLM 요금 장부: %s %s 기록 %d건을 옮김", market, strategy or "주 계좌", moved)
    return moved


# ====================================================================== 장부 보기
GROUP_KEYS = ("profile", "provider", "model", "market", "strategy", "kind")
_CACHE: WeakKeyDictionary[Database, dict[tuple[Any, ...], dict[str, Any]]] = WeakKeyDictionary()
_LOCK = threading.Lock()


def _month_keys(today: date, months: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "input": 0,
        "cache_read": 0,
        "cache_known": 0,  # 캐시 적중을 제공사가 알려 준 호출 수
        "output": 0,
        "reasoning": 0,
        "cost": 0.0,
        "unpriced": 0,
        "by_source": defaultdict(int),
    }


def _add(b: dict[str, Any], r: Any) -> None:
    b["calls"] += r.calls
    b["input"] += r.input_tokens
    b["output"] += r.output_tokens
    b["reasoning"] += r.reasoning_tokens or 0
    if r.cache_read_tokens is not None:
        b["cache_read"] += r.cache_read_tokens
        b["cache_known"] += r.calls
    if r.cost_usd is None:
        b["unpriced"] += r.calls
    else:
        b["cost"] += r.cost_usd
    b["by_source"][r.cost_source or "unpriced"] += r.calls


def _rows_out(groups: dict[tuple[Any, ...], dict[str, Any]], first: str) -> list[dict[str, Any]]:
    out = []
    for key, b in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        out.append(
            {
                first: key[0],
                **dict(zip(GROUP_KEYS, key[1:], strict=True)),
                **{k: v for k, v in b.items() if k not in ("cost", "by_source")},
                "cost": round(b["cost"], 6),
                "by_source": dict(b["by_source"]),
            }
        )
    return out


def ledger_view(db: Database, now: datetime, months: int = 12, month: str | None = None) -> dict[str, Any]:
    """월별 묶음(최근 months 개월)과 고른 달(month, 기본 이번 달)의 날짜별 묶음.

    묶음 열쇠: profile·provider·model·market·strategy·kind — 화면이 이것으로 거르고 다시 묶는다.
    결과는 (오늘, 기간, 달, 장부 건수·마지막 id)가 같으면 캐시를 돌려준다(B5).
    """
    today = now.astimezone(KST).date()
    keys = _month_keys(today, months)
    sel = month if month in keys else keys[-1]
    start = datetime(int(keys[0][:4]), int(keys[0][5:]), 1, tzinfo=KST)
    with db.session() as s:
        count, last_id = s.execute(select(func.count(LlmUsage.id), func.max(LlmUsage.id))).one()
    ck = (today, months, sel, int(count or 0), int(last_id or 0))
    with _LOCK:
        hit = _CACHE.get(db, {}).get(ck)
    if hit is not None:
        return copy.deepcopy(hit)

    cols = (
        LlmUsage.ts,
        LlmUsage.calls,
        LlmUsage.input_tokens,
        LlmUsage.output_tokens,
        LlmUsage.reasoning_tokens,
        LlmUsage.cache_read_tokens,
        LlmUsage.cost_usd,
        LlmUsage.cost_source,
        *(getattr(LlmUsage, k) for k in GROUP_KEYS),
    )
    monthly: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(_bucket)
    daily: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(_bucket)
    with db.session() as s:
        for r in s.execute(select(*cols).where(LlmUsage.ts >= start)):
            day = _kst_day(r.ts)
            mk = day.strftime("%Y-%m")
            if mk not in keys:
                continue
            g = (
                r.profile or "",
                r.provider or "",
                public_model(r.model),
                r.market or "",
                r.strategy or "",
                r.kind,
            )
            _add(monthly[(mk, *g)], r)
            if mk == sel:
                _add(daily[(day.isoformat(), *g)], r)
    out = {
        "checked": PRICES_CHECKED,
        "sources": SOURCES,
        "today": today.isoformat(),
        "months": keys,
        "month": sel,
        "groups": _rows_out(monthly, "month"),
        "daily": _rows_out(daily, "day"),
    }
    with _LOCK:
        per = _CACHE.setdefault(db, {})
        if len(per) >= 16:
            per.clear()
        per[ck] = copy.deepcopy(out)
    return out
