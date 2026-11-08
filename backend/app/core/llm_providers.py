"""유료 LLM 제공사 판별 (D-45, D-66).

.env 의 LLM_BASE_URL 주소로 먼저 판단하고, 주소가 비어 있으면 키 모양으로 판단한다.
- 주소: api.openai.com → openai, generativelanguage.googleapis.com → google, openrouter.ai → openrouter
- 키: sk-or- → openrouter, sk- → openai, AIza·AQ. → google
  (OpenRouter 키는 sk-or-v1- 로 시작한다: https://openrouter.ai/docs/guides/community/replit —
   일반 sk- 보다 먼저 본다)
그 밖(자체 서버·게이트웨이)은 None — 모델 목록 없이 .env 의 LLM_MODEL 만 쓴다.

Anthropic 은 쓰지 않기로 했다(D-66): 목록·판별에서 뺐고, sk-ant- 키는 OpenAI 로 잘못 알지 않도록 None 으로 둔다.

OpenRouter 는 OpenAI 호환 Chat Completions 로 부른다(공식 주소 https://openrouter.ai/api/v1,
https://openrouter.ai/docs/quickstart). 모델 목록은 app/agent/openrouter.py 가 OpenRouter 목록 API 로 만든다.
"""

from __future__ import annotations

from urllib.parse import urlparse

LABEL = {"openai": "OpenAI", "google": "Google", "openrouter": "OpenRouter", "qwen": "Qwen Cloud"}
# Qwen Cloud (D-73): 지역마다 주소·키가 따로다 — {region}.maas.qwencloudapi.com/compatible-mode/v1
# (https://docs.qwencloud.com/developer-guides/getting-started/regions). maas.qwencloudapi.com = 싱가포르
QWEN_REGIONS = ("ap-southeast-1", "us-east-1", "eu-central-1", "ap-northeast-1", "cn-hongkong", "cn-beijing")
QWEN_HOST_SUFFIX = "maas.qwencloudapi.com"


def qwen_base_url(region: str) -> str:
    r = (region or "ap-southeast-1").strip()
    if r not in QWEN_REGIONS:
        raise ValueError(f"Qwen Cloud 지역이 이상해요: {r!r} ({', '.join(QWEN_REGIONS)})")
    return f"https://{r}.{QWEN_HOST_SUFFIX}/compatible-mode/v1"


# 주소를 비워 두면 쓰는 공식 주소 (OpenAI 호환 호출 방식)
BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "qwen": "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1",
}
HOSTS = {
    "api.openai.com": "openai",
    "generativelanguage.googleapis.com": "google",
    "openrouter.ai": "openrouter",
}
SUPPORTED = frozenset(BASE_URL)


def detect(base_url: str, api_key: str) -> str | None:
    base = (base_url or "").strip()
    if base:
        host = (urlparse(base).hostname or "").lower()
        if host == QWEN_HOST_SUFFIX or host.endswith("." + QWEN_HOST_SUFFIX):
            return "qwen"
        return HOSTS.get(host)
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):  # Anthropic 키 — 쓰지 않는 제공사 (sk- 라고 OpenAI 로 보지 않는다)
        return None
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
