"""유료 LLM 제공사 판별 (D-45).

.env 의 LLM_BASE_URL 주소로 먼저 판단하고, 주소가 비어 있으면 키 모양으로 판단한다.
- 주소: api.openai.com → openai, generativelanguage.googleapis.com → google, api.anthropic.com → anthropic,
        openrouter.ai → openrouter
- 키: sk-ant- → anthropic, sk-or- → openrouter, sk- → openai, AIza·AQ. → google
  (OpenRouter 키는 sk-or-v1- 로 시작한다: https://openrouter.ai/docs/guides/community/replit —
   일반 sk- 보다 먼저 본다)
그 밖(자체 서버·게이트웨이)은 None — 모델 목록 없이 .env 의 LLM_MODEL 만 쓴다.

Anthropic 은 공식 권장이 전용 Messages API 이고, 최신 모델(Opus 5.5·Sonnet 5.5)은 이 앱이 세션 마지막에 쓰는
도구 강제 호출(tool_choice)을 거절한다. 전용 연결을 만들기 전까지는 목록에만 보이고 고를 수 없다.

OpenRouter 는 OpenAI 호환 Chat Completions 로 부른다(공식 주소 https://openrouter.ai/api/v1,
https://openrouter.ai/docs/quickstart). 모델 목록은 app/agent/openrouter.py 가 OpenRouter 목록 API 로 만든다.
"""

from __future__ import annotations

from urllib.parse import urlparse

LABEL = {"openai": "OpenAI", "google": "Google", "anthropic": "Anthropic", "openrouter": "OpenRouter"}
# 주소를 비워 두면 쓰는 공식 주소 (OpenAI 호환 호출 방식)
BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}
HOSTS = {
    "api.openai.com": "openai",
    "generativelanguage.googleapis.com": "google",
    "api.anthropic.com": "anthropic",
    "openrouter.ai": "openrouter",
}
SUPPORTED = frozenset(BASE_URL)
UNSUPPORTED_REASON = {"anthropic": "Anthropic 전용 API 연결 전이라 아직 못 써요"}


def detect(base_url: str, api_key: str) -> str | None:
    base = (base_url or "").strip()
    if base:
        return HOSTS.get((urlparse(base).hostname or "").lower())
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("sk-"):
        return "openai"
    if key.startswith(("AIza", "AQ.")):
        return "google"
    return None


def resolve_base_url(base_url: str, api_key: str) -> str:
    """주소가 있으면 그대로, 비어 있으면 키로 알아낸 공식 주소(없으면 빈 문자열)."""
    base = (base_url or "").strip()
    if base:
        return base
    return BASE_URL.get(detect("", api_key) or "", "")
