"""OpenAI 호환 Chat Completions 클라이언트 (사용자가 .env 에 base_url / model / api_key 지정).

SDK 없이 httpx 로 직접 호출한다 — 제공자 교체가 쉽고 의존성이 줄어든다(D-11).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.core.http import Route, SafeHttpClient

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class ToolsUnsupportedError(LLMError):
    """제공자가 tool calling 을 지원하지 않음."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""


def llm_routes(base_url: str) -> list[Route]:
    parts = urlsplit(base_url)
    path = (parts.path or "").rstrip("/") + "/chat/completions"
    return [Route((parts.hostname or "").lower(), "POST", re.compile(re.escape(path)))]


_THINK = re.compile(r"<think>.*?(</think>|$)", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """추론형 모델(Qwen3 등)이 본문에 섞어 보내는 <think>…</think> 블록 제거."""
    return _THINK.sub("", text or "").strip()


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"value": v}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                v = json.loads(m.group(0))
                return v if isinstance(v, dict) else {}
            except json.JSONDecodeError:
                pass
        return {"_unparsed": raw[:500]}


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 180.0,
        http: SafeHttpClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.http = http or SafeHttpClient(
            llm_routes(self.base_url), rate_per_sec=2, timeout=timeout, max_retries=3
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if self.max_tokens > 0:
            body["max_tokens"] = self.max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        r = await self.http.request(
            "POST",
            f"{self.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            text = r.text[:600]
            if tools and r.status_code in (400, 404, 422) and re.search(r"tool|function", text, re.I):
                raise ToolsUnsupportedError(f"tool calling 미지원 응답({r.status_code}): {text[:200]}")
            raise LLMError(f"LLM HTTP {r.status_code}: {text[:300]}")
        try:
            data = r.json()
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(f"LLM 응답 형식 오류: {r.text[:200]}") from e
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            calls.append(
                ToolCall(
                    id=tc.get("id") or f"call_{i}",
                    name=fn.get("name", ""),
                    arguments=_parse_args(raw),
                    raw_arguments=raw if isinstance(raw, str) else "",
                )
            )
        usage = {k: int(v) for k, v in (data.get("usage") or {}).items() if isinstance(v, int)}
        return ChatResult(
            content=strip_reasoning(msg.get("content") or ""),
            tool_calls=calls,
            usage=usage,
            finish_reason=str(choice.get("finish_reason", "")),
        )


def extract_json(text: str) -> dict[str, Any] | None:
    """모델 출력에서 JSON 객체 하나를 뽑는다 (```json 블록 우선)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    cands = [m.group(1)] if m else []
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        cands.append(text[first : last + 1])
    for c in cands:
        try:
            v = json.loads(c)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue
    return None
