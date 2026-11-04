"""OpenRouter 모델 목록 (D-45 확장): 유료 LLM 키가 OpenRouter 면 화면의 모델 목록을 여기서 만든다.

OpenRouter 는 모델이 수백 개라 정적 가격표(app/agent/costs.py) 대신 공식 목록 API 를 불러와 메모리에 둔다(6시간).

공식 문서로 확인한 것 (2026-11 기준):
- 목록: GET https://openrouter.ai/api/v1/models
  https://openrouter.ai/docs/api/api-reference/models/get-models
  https://openrouter.ai/docs/guides/overview/models
  · offset·limit 을 안 주면 전체 목록. supported_parameters=tools 로 도구 호출 지원 모델만 거를 수 있다.
  · 인증: API 레퍼런스는 Bearer 키(apiKey)를 적어 두고 안내 페이지 예시는 키 없이 부른다 → 키가 있으면 붙인다.
  · 응답 data[]: id, name, context_length, pricing.prompt / pricing.completion(문자열, 토큰당 USD),
    supported_parameters(문자열 배열: tools = 함수 호출, tool_choice = 도구 선택 강제) 등.
- 대화: POST https://openrouter.ai/api/v1/chat/completions (OpenAI 호환, tools·tool_choice 지원)
  https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request

에이전트는 도구 호출을 쓰고 세션 마지막에 도구를 강제(tool_choice)하므로
supported_parameters 에 tools 와 tool_choice 가 둘 다 있는 모델만 고를 수 있게 한다.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from app.core.http import Route, SafeHttpClient

HOST = "openrouter.ai"
MODELS_URL = f"https://{HOST}/api/v1/models"
ROUTES = [Route(HOST, "GET", re.compile(r"/api/v1/models"))]
REQUIRED_PARAMS = ("tools", "tool_choice")
TTL_SEC = 6 * 3600
MIN_REFETCH_SEC = 30  # 새로고침을 연달아 눌러도 30초 안에는 다시 부르지 않는다
log = logging.getLogger(__name__)


class OpenRouterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ORModel:
    id: str
    name: str
    group: str  # id 의 '/' 앞 (openai, anthropic, google, deepseek, qwen …)
    group_label: str  # 이름의 ': ' 앞 (OpenAI, Anthropic, Google …), 없으면 group
    input: float | None  # USD / 1M 토큰
    output: float | None
    context_length: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def per_million(v: Any) -> float | None:
    """토큰당 USD 문자열 → 1M 토큰당 USD. 음수(가변 가격 라우터 등)·숫자 아님은 None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0:
        return None
    return round(f * 1_000_000, 4)


def parse(data: Any) -> list[ORModel]:
    """GET /models 응답 → 도구 호출(tools + tool_choice)을 지원하는 모델만, (회사, 이름) 순."""
    items = data.get("data") if isinstance(data, dict) else None
    out: dict[str, ORModel] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or "").strip()
        params = it.get("supported_parameters") or []
        if not mid or not all(p in params for p in REQUIRED_PARAMS):
            continue
        raw_name = str(it.get("name") or mid).strip()
        vendor, sep, short = raw_name.partition(": ")
        group = mid.split("/", 1)[0] if "/" in mid else "기타"
        pricing = it.get("pricing") or {}
        ctx = it.get("context_length")
        out[mid] = ORModel(
            id=mid,
            name=short.strip() if sep and short.strip() else raw_name,
            group=group,
            group_label=vendor.strip() if sep and vendor.strip() else group,
            input=per_million(pricing.get("prompt")),
            output=per_million(pricing.get("completion")),
            context_length=int(ctx) if isinstance(ctx, int | float) and ctx > 0 else None,
        )
    return sorted(out.values(), key=lambda m: (m.group_label.lower(), m.name.lower(), m.id))


async def fetch_models(api_key: str = "", http: SafeHttpClient | None = None) -> list[ORModel]:
    client = http or SafeHttpClient(ROUTES, rate_per_sec=1, timeout=20, max_retries=2)
    headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}
    try:
        r = await client.request("GET", MODELS_URL, params={"supported_parameters": "tools"}, headers=headers)
    finally:
        if http is None:
            await client.aclose()
    if r.status_code == 401:
        raise OpenRouterError("OpenRouter 키 인증 실패(HTTP 401) — .env 의 LLM_API_KEY 를 확인하세요")
    if r.status_code != 200:
        raise OpenRouterError(f"OpenRouter 모델 목록 HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise OpenRouterError("OpenRouter 모델 목록 응답 형식 오류") from e
    return parse(body)


class ModelCache:
    """프로세스 메모리에 두는 목록. 비어 있거나 6시간이 지나면 화면이 새로고침을 부른다."""

    def __init__(self, ttl: float = TTL_SEC) -> None:
        self.ttl = ttl
        self.clear()

    def clear(self) -> None:
        self.models: list[ORModel] = []
        self.ids: frozenset[str] = frozenset()
        self.fetched_at: float | None = None  # time.time()
        self.error: str | None = None
        self._tried: float = 0.0  # time.monotonic()

    @property
    def loaded(self) -> bool:
        return self.fetched_at is not None

    @property
    def stale(self) -> bool:
        return self.fetched_at is None or time.time() - self.fetched_at > self.ttl

    def has(self, model_id: str) -> bool:
        return model_id in self.ids

    def get(self, model_id: str) -> ORModel | None:
        return next((m for m in self.models if m.id == model_id), None)

    def store(self, models: list[ORModel]) -> None:
        self.models = models
        self.ids = frozenset(m.id for m in models)
        self.fetched_at = time.time()
        self.error = None

    async def refresh(
        self, api_key: str = "", *, force: bool = False, http: SafeHttpClient | None = None
    ) -> bool:
        """새로 불러오면 True. 실패하면 이전 목록을 그대로 두고 error 에 이유를 남긴다(키 값은 남기지 않는다)."""
        now = time.monotonic()
        if not force and not self.stale:
            return False
        if self._tried and now - self._tried < MIN_REFETCH_SEC and self.loaded and not self.error:
            return False
        self._tried = now
        try:
            models = await fetch_models(api_key, http)
        except OpenRouterError as e:
            self.error = str(e)
        except Exception as e:  # 네트워크 등
            self.error = f"OpenRouter 모델 목록을 못 불러왔어요({type(e).__name__})"
        else:
            if models:
                self.store(models)
                return True
            self.error = "OpenRouter 목록에 도구 호출을 지원하는 모델이 없어요"
        log.warning("OpenRouter 모델 목록 실패: %s", self.error)
        return False


CACHE = ModelCache()
