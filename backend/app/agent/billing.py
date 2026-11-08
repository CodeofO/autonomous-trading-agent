"""요금 장부를 제공사 공식 기록과 맞춰 보기 (D-66).

공식 문서로 확인한 조회 경로 (2026-11-05):
- OpenRouter: GET https://openrouter.ai/api/v1/key — 일반 API 키로 그 키의 usage_daily·usage_weekly·usage_monthly
  (USD, UTC 기준, 주는 월~일). 키 단위 합계라 같은 키를 쓰는 다른 앱 사용분도 들어간다.
  https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key
  (모델별·날짜별 활동 /api/v1/activity 는 관리용 키가 따로 있어야 해서 쓰지 않는다)
- OpenAI: 조직 Usage API GET /v1/organization/usage/completions (bucket_width=1d, group_by=model) 와
  Costs API GET /v1/organization/costs (bucket_width=1d, group_by=line_item — 모델은 line_item 앞부분).
  Admin 키(OPENAI_ADMIN_KEY)가 있어야 하고 조직 전체 기록이다(다른 프로젝트·앱 포함).
  https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions
  https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
- Google(Gemini API 키): 키로 조회하는 사용량·요금 API 가 없어 장부는 공식 단가로 계산한 값이다.

키 값은 어디에도 남기지 않는다 (로그·응답·오류 문구 모두).
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.agent.costs import catalog_price, public_model
from app.core import llm_providers as providers
from app.core.config import Settings
from app.core.http import Route, SafeHttpClient
from app.db.models import LlmUsage
from app.db.session import Database

log = logging.getLogger(__name__)

OR_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENAI_USAGE_URL = "https://api.openai.com/v1/organization/usage/completions"
OPENAI_COSTS_URL = "https://api.openai.com/v1/organization/costs"
ROUTES = [
    Route("openrouter.ai", "GET", re.compile(r"^/api/v1/key$")),
    Route("api.openai.com", "GET", re.compile(r"^/v1/organization/(usage/completions|costs)$")),
]
TTL_SEC = 600.0  # 공식 기록은 10분 동안 다시 묻지 않는다
MIN_REFRESH_SEC = 30.0
MAX_PAGES = 6
LABEL = {"openai": "OpenAI", "openrouter": "OpenRouter", "google": "Google", "qwen": "Qwen Cloud"}

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


class BillingError(RuntimeError):
    """화면에 보여 줄 한국어 사유 (키 값은 넣지 않는다)."""


def month_window(month: str) -> tuple[datetime, datetime]:
    """'2026-11' → UTC [그달 1일 0시, 다음 달 1일 0시). 공식 기록이 UTC 날짜 단위라 장부도 같은 창으로 센다."""
    y, m = int(month[:4]), int(month[5:7])
    start = datetime(y, m, 1, tzinfo=UTC)
    end = datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=UTC)
    return start, end


def ledger_window(db: Database, provider: str, start: datetime, end: datetime) -> dict[str, Any]:
    """장부에서 그 제공사·기간(UTC)의 합계와 모델별 합계."""
    cols = (
        LlmUsage.model,
        func.sum(LlmUsage.calls),
        func.sum(LlmUsage.input_tokens),
        func.sum(func.coalesce(LlmUsage.cache_read_tokens, 0)),
        func.sum(LlmUsage.output_tokens),
        func.sum(func.coalesce(LlmUsage.cost_usd, 0.0)),
    )
    with db.session() as s:
        rows = s.execute(
            select(*cols)
            .where(LlmUsage.provider == provider, LlmUsage.ts >= start, LlmUsage.ts < end)
            .group_by(LlmUsage.model)
        ).all()
    models: dict[str, dict[str, Any]] = {}
    total = {"calls": 0, "input": 0, "cache_read": 0, "output": 0, "cost": 0.0}
    for model, calls, inp, hit, out, cost in rows:
        key = _model_key(public_model(model))
        m = models.setdefault(key, {"calls": 0, "input": 0, "cache_read": 0, "output": 0, "cost": 0.0})
        for k, v in (("calls", calls), ("input", inp), ("cache_read", hit), ("output", out), ("cost", cost)):
            m[k] += v or 0
            total[k] += v or 0
    return {"total": _round(total), "models": {k: _round(v) for k, v in models.items()}}


def _round(d: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 6) if isinstance(v, float) else int(v) for k, v in d.items()}


def _model_key(model: str) -> str:
    """같은 모델의 다른 이름(openai/ 앞부분·날짜 꼬리)을 단가표 id 하나로."""
    mp = catalog_price(model)
    return mp.id if mp else model.rsplit("/", 1)[-1]


# ====================================================================== OpenRouter
def openrouter_keys(settings: Settings, test_base_url: str = "") -> list[tuple[str, str]]:
    """대조할 OpenRouter 키: (이름, 키). 유료 LLM 이 OpenRouter 면 그 키, 테스트용 LLM 이 OpenRouter 면 그 키(다를 때만)."""
    out: list[tuple[str, str]] = []
    dedicated = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    if dedicated:  # OPENROUTER_API_KEY (D-71)
        out.append(("OpenRouter 키", dedicated))
    paid = settings.llm_api_key.get_secret_value().strip() if settings.llm_api_key else ""
    if paid and paid != dedicated and providers.detect(settings.llm_base_url, paid) == "openrouter":
        out.append(("유료 LLM 키", paid))
    test = settings.llm_test_api_key.get_secret_value().strip() if settings.llm_test_api_key else ""
    base = test_base_url or settings.llm_test_base_url
    if test and test not in (paid, dedicated) and providers.detect(base, test) == "openrouter":
        out.append(("테스트용 LLM 키", test))
    return out


async def _get(http: SafeHttpClient, url: str, key: str, params: Any = None) -> dict[str, Any]:
    r = await http.request("GET", url, params=params, headers={"Authorization": f"Bearer {key}"})
    if r.status_code in (401, 403):
        raise BillingError(f"키 권한이 없어요(HTTP {r.status_code})")
    if r.status_code != 200:
        raise BillingError(f"공식 기록 조회 실패(HTTP {r.status_code})")
    try:
        body = r.json()
    except ValueError as e:
        raise BillingError("공식 기록 응답 형식 오류") from e
    if not isinstance(body, dict):
        raise BillingError("공식 기록 응답 형식 오류")
    return body


def _f(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


async def openrouter_report(
    db: Database, keys: list[tuple[str, str]], now: datetime, http: SafeHttpClient
) -> dict[str, Any]:
    out: dict[str, Any] = {"provider": "openrouter", "label": LABEL["openrouter"], "windows": []}
    if not keys:
        return out | {"status": "no_key", "note": "OpenRouter 키를 쓰지 않아요."}
    now = now.astimezone(UTC)
    day0 = datetime(now.year, now.month, now.day, tzinfo=UTC)
    week0 = day0 - timedelta(days=now.weekday())  # 월요일 0시 (UTC)
    month0 = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = now + timedelta(seconds=1)
    ledger = {
        "daily": ledger_window(db, "openrouter", day0, end)["total"],
        "weekly": ledger_window(db, "openrouter", week0, end)["total"],
        "monthly": ledger_window(db, "openrouter", month0, end)["total"],
    }
    official = {"daily": 0.0, "weekly": 0.0, "monthly": 0.0}
    for _label, key in keys:
        data = (await _get(http, OR_KEY_URL, key)).get("data") or {}
        official["daily"] += _f(data.get("usage_daily"))
        official["weekly"] += _f(data.get("usage_weekly"))
        official["monthly"] += _f(data.get("usage_monthly"))
    names = {"daily": "오늘", "weekly": "이번 주(월~)", "monthly": "이번 달"}
    for w in ("daily", "weekly", "monthly"):
        out["windows"].append(
            {
                "key": w,
                "label": names[w],
                "official": {"cost": round(official[w], 6)},
                "ledger": ledger[w],
            }
        )
    return out | {
        "status": "ok",
        "note": (
            f"키 {len(keys)}개의 공식 사용 금액(UTC 기준)이에요. 같은 키를 쓰는 다른 앱 사용분도 포함돼요. "
            "호출마다 OpenRouter 가 적어 준 요금을 그대로 장부에 남겨요."
        ),
    }


# ====================================================================== OpenAI
async def _pages(http: SafeHttpClient, url: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    page: str | None = None
    for _ in range(MAX_PAGES):
        body = await _get(http, url, key, {**params, **({"page": page} if page else {})})
        buckets += [b for b in body.get("data") or [] if isinstance(b, dict)]
        page = body.get("next_page") if body.get("has_more") else None
        if not page:
            break
    return buckets


async def openai_report(
    db: Database, admin_key: str, month: str, now: datetime, http: SafeHttpClient
) -> dict[str, Any]:
    out: dict[str, Any] = {"provider": "openai", "label": LABEL["openai"], "rows": []}
    start, end = month_window(month)
    end = min(end, now.astimezone(UTC) + timedelta(seconds=1))
    led = ledger_window(db, "openai", start, end)
    if not admin_key:
        return out | {
            "status": "no_key",
            "note": (
                "OpenAI 공식 사용량·요금은 Admin 키로만 조회돼요. .env 에 OPENAI_ADMIN_KEY 를 넣고 "
                "다시 시작하면 여기서 맞춰 봐요. 지금은 응답의 토큰(캐시 포함)을 공식 단가로 계산한 값이에요."
            ),
            "ledger": led["total"],
        }
    base = {"start_time": int(start.timestamp()), "end_time": int(end.timestamp()), "bucket_width": "1d"}
    usage = await _pages(http, OPENAI_USAGE_URL, admin_key, {**base, "group_by": "model", "limit": 31})
    cost_buckets = await _pages(
        http, OPENAI_COSTS_URL, admin_key, {**base, "group_by": "line_item", "limit": 31}
    )
    official: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"requests": 0, "input": 0, "cached": 0, "output": 0, "cost": 0.0}
    )
    for b in usage:
        for r in b.get("results") or []:
            m = official[_model_key(str(r.get("model") or "기타"))]
            m["requests"] += int(_f(r.get("num_model_requests")))
            m["input"] += int(_f(r.get("input_tokens")))
            m["cached"] += int(_f(r.get("input_cached_tokens")))
            m["output"] += int(_f(r.get("output_tokens")))
    other = 0.0
    for b in cost_buckets:
        for r in b.get("results") or []:
            amount = _f((r.get("amount") or {}).get("value"))
            item = str(r.get("line_item") or "")
            model = item.split(",", 1)[0].strip() if "," in item else ""
            if model:
                official[_model_key(model)]["cost"] += amount
            else:
                other += amount
    keys = sorted(set(official) | set(led["models"]))
    zero = {"calls": 0, "input": 0, "cache_read": 0, "output": 0, "cost": 0.0}
    for k in keys:
        out["rows"].append(
            {
                "model": k,
                "official": _round(official[k]) if k in official else None,
                "ledger": led["models"].get(k, zero),
            }
        )
    total_official = sum(v["cost"] for v in official.values()) + other
    return out | {
        "status": "ok",
        "note": (
            "조직 전체 공식 기록(UTC 날짜)이에요. 같은 조직의 다른 프로젝트·앱 사용분도 들어가요. "
            "공식 요금은 하루 단위로 늦게 반영될 수 있어요."
        ),
        "official_total": round(total_official, 6),
        "official_other": round(other, 6),
        "ledger": led["total"],
    }


# ====================================================================== Google
def google_report(db: Database, month: str, now: datetime) -> dict[str, Any]:
    start, end = month_window(month)
    led = ledger_window(db, "google", start, min(end, now.astimezone(UTC) + timedelta(seconds=1)))
    return {
        "provider": "google",
        "label": LABEL["google"],
        "status": "unsupported",
        "note": (
            "Gemini API 키로 조회하는 공식 사용량·요금 API 가 없어요(AI Studio 화면·Cloud 결제 내보내기만). "
            "장부는 응답의 토큰을 공식 단가로 계산한 값이에요."
        ),
        "ledger": led["total"],
    }


# ====================================================================== Qwen Cloud
def qwen_report(db: Database, month: str, now: datetime) -> dict[str, Any]:
    start, end = month_window(month)
    led = ledger_window(db, "qwen", start, min(end, now.astimezone(UTC) + timedelta(seconds=1)))
    return {
        "provider": "qwen",
        "label": LABEL["qwen"],
        "status": "unsupported",
        "note": (
            "Qwen Cloud 공식 문서에 키로 조회하는 사용량·요금 API 가 없어요(콘솔 화면만). "
            "장부는 응답의 토큰을 공식 단가(싱가포르 정가)로 계산한 값이에요."
        ),
        "ledger": led["total"],
    }


# ====================================================================== 묶어서
async def official_report(
    db: Database,
    settings: Settings,
    month: str,
    now: datetime,
    *,
    refresh: bool = False,
    test_base_url: str = "",
    http: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """제공사별 공식 기록 대조. 10분 캐시(refresh 면 30초 지난 것만 다시)."""
    admin = settings.openai_admin_key.get_secret_value().strip() if settings.openai_admin_key else ""
    keys = openrouter_keys(settings, test_base_url)
    client = http or SafeHttpClient(ROUTES, rate_per_sec=2, timeout=20, max_retries=1)
    out: list[dict[str, Any]] = []
    try:
        for prov in ("openai", "openrouter", "google", "qwen"):
            ck = (prov, month)
            hit = _cache.get(ck)
            age = time.monotonic() - hit[0] if hit else None
            if hit and age is not None and (age < MIN_REFRESH_SEC or (not refresh and age < TTL_SEC)):
                out.append(hit[1])
                continue
            try:
                if prov == "openai":
                    rep = await openai_report(db, admin, month, now, client)
                elif prov == "openrouter":
                    rep = await openrouter_report(db, keys, now, client)
                elif prov == "google":
                    rep = google_report(db, month, now)
                else:
                    rep = qwen_report(db, month, now)
            except BillingError as e:
                rep = {"provider": prov, "label": LABEL[prov], "status": "error", "note": str(e)}
            except Exception as e:  # 네트워크 등 — 키나 주소는 남기지 않는다
                log.warning("공식 기록 조회 실패 %s: %s", prov, type(e).__name__)
                rep = {
                    "provider": prov,
                    "label": LABEL[prov],
                    "status": "error",
                    "note": f"공식 기록을 불러오지 못했어요({type(e).__name__})",
                }
            rep["fetched_at"] = datetime.now(UTC).isoformat()
            if rep["status"] != "error":
                _cache[ck] = (time.monotonic(), rep)
            out.append(rep)
    finally:
        if http is None:
            await client.aclose()
    return {"month": month, "window_tz": "UTC", "providers": out}
