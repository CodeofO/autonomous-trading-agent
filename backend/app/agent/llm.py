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

import httpx

from app.core import llm_providers as providers
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
    # prompt_tokens·completion_tokens·total_tokens (+ 제공사가 알려 주면 cached_tokens·cache_write_tokens·
    # reasoning_tokens — 알려 주지 않으면 키가 없다)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""
    # Responses API 의 output 항목 그대로 (추론 항목 포함). 도구 결과와 함께 다음 요청에 되돌려 보낸다
    raw_output: list[dict[str, Any]] = field(default_factory=list)
    # 제공사가 응답에 적어 준 이 호출의 요금(USD) — OpenRouter usage.cost (D-66). 없으면 None
    cost: float | None = None
    # 응답 id·실제로 답한 모델·(OpenRouter) 공급사·서비스 등급 — 요금 장부·공식 기록 대조용 (D-66)
    meta: dict[str, str] = field(default_factory=dict)


# 대화 메시지에 붙여 두는 Responses API 원본 항목 키 (Chat Completions 로 보낼 때는 빠진다)
RAW_KEY = "_raw_output"


def llm_routes(base_url: str) -> list[Route]:
    parts = urlsplit(base_url)
    host, base = (parts.hostname or "").lower(), (parts.path or "").rstrip("/")
    return [Route(host, "POST", re.compile(re.escape(base + p))) for p in ("/chat/completions", "/responses")]


def resolve_api(api: str, base_url: str) -> str:
    """auto → OpenAI 본가(api.openai.com)는 Responses API, 그 밖(vLLM·Gemini 호환 등)은 Chat Completions."""
    if api in ("chat", "responses"):
        return api
    return "responses" if (urlsplit(base_url).hostname or "").lower() == "api.openai.com" else "chat"


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat Completions 형식 대화 → Responses API input 항목."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content") or "",
                }
            )
        elif role == "assistant" and m.get(RAW_KEY):
            out.extend(m[RAW_KEY])  # 추론·도구 호출 항목을 받은 그대로 되돌려 보낸다 (공식 권장)
        elif role == "assistant" and m.get("tool_calls"):
            if m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                out.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


def to_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        fn = t.get("function") or {}
        out.append(
            {
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def to_responses_tool_choice(choice: str | dict[str, Any] | None) -> str | dict[str, Any]:
    if isinstance(choice, dict) and choice.get("type") == "function":
        return {
            "type": "function",
            "name": (choice.get("function") or {}).get("name") or choice.get("name", ""),
        }
    return choice or "auto"


def parse_responses(data: dict[str, Any]) -> ChatResult:
    output = list(data.get("output") or [])
    texts: list[str] = []
    calls: list[ToolCall] = []
    for item in output:
        if item.get("type") == "message":
            texts += [c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text"]
        elif item.get("type") == "function_call":
            raw = item.get("arguments")
            calls.append(
                ToolCall(
                    id=item.get("call_id") or item.get("id") or f"call_{len(calls)}",
                    name=item.get("name", ""),
                    arguments=_parse_args(raw),
                    raw_arguments=raw if isinstance(raw, str) else "",
                )
            )
    u = data.get("usage") or {}
    usage = {
        "prompt_tokens": int(u.get("input_tokens") or 0),
        "completion_tokens": int(u.get("output_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }
    ind = _obj(u.get("input_tokens_details"))
    cached = _num(ind.get("cached_tokens"))
    if cached is not None:
        usage["cached_tokens"] = int(cached)
    write = _num(ind.get("cache_write_tokens"))
    if write is not None:
        usage["cache_write_tokens"] = int(write)
        w12 = _num(_obj(ind.get("cache_write_tokens_details")).get("ttl_12h_tokens"))
        if w12:
            usage["cache_write_12h_tokens"] = int(w12)
    reasoning = _num((u.get("output_tokens_details") or {}).get("reasoning_tokens"))
    if reasoning is not None:
        usage["reasoning_tokens"] = int(reasoning)
    if data.get("status") == "incomplete":
        finish = (
            "length"
            if (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
            else "incomplete"
        )
    else:
        finish = "tool_calls" if calls else "stop"
    return ChatResult(
        content="\n".join(t for t in texts if t).strip(),
        tool_calls=calls,
        usage=usage,
        finish_reason=finish,
        raw_output=output,
        meta=response_meta(data),
    )


def _obj(v: Any) -> dict[str, Any]:
    """JSON 객체만 (그 밖은 빈 dict)."""
    return v if isinstance(v, dict) else {}


def _num(v: Any) -> float | None:
    """JSON 숫자만 (bool·문자열·None 은 None)."""
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def parse_chat_usage(u: Any) -> tuple[dict[str, int], float | None]:
    """Chat Completions 의 usage → (토큰 dict, 제공사가 적어 준 요금 USD).

    위 단계 정수 필드(prompt_tokens·completion_tokens·total_tokens 등)는 그대로, 아래 단계에서
    prompt_tokens_details.cached_tokens·cache_write_tokens, completion_tokens_details.reasoning_tokens 를 꺼낸다.
    OpenRouter 는 usage.cost(USD)를 준다. 제공사가 주지 않은 값은 키를 만들지 않는다(0 과 '모름'을 구분).
    """
    if not isinstance(u, dict):
        return {}, None
    out = {k: int(v) for k, v in u.items() if isinstance(v, int) and not isinstance(v, bool)}
    pd, cd = _obj(u.get("prompt_tokens_details")), _obj(u.get("completion_tokens_details"))
    for key, src in (("cached_tokens", pd), ("cache_write_tokens", pd), ("reasoning_tokens", cd)):
        v = _num(src.get(key))
        if v is not None:
            out[key] = int(v)
    cost = _num(u.get("cost"))
    return out, cost


def response_meta(data: dict[str, Any]) -> dict[str, str]:
    """응답 id·실제 모델·공급사(OpenRouter)·서비스 등급(OpenAI) 중 있는 것만."""
    out: dict[str, str] = {}
    for key in ("id", "model", "provider", "service_tier"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()[:128]
    return out


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
        api: str = "auto",
        reasoning_effort: str = "",
        profile: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile  # free | paid (D-43) — 요금 장부에 남긴다 (D-66)
        self.api = resolve_api(api, base_url)  # chat | responses
        self.reasoning_effort = reasoning_effort.strip()
        self.model = model
        self._api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.http = http or SafeHttpClient(
            llm_routes(self.base_url), rate_per_sec=2, timeout=timeout, max_retries=3
        )
        # 모델마다 받는 매개변수가 다르다 (OpenAI 추론형: max_tokens → max_completion_tokens, temperature 고정).
        # 서버가 'Unsupported parameter' 로 알려 주면 그에 맞춰 바꾸고 기억한다.
        self._token_param = "max_tokens"
        self._send_temperature = True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """`max_tokens`: 이 요청만의 출력 한도 (없으면 설정값). 추론형 모델의 답이 잘렸을 때 늘려 다시 묻는 데 쓴다."""
        limit = self.max_tokens if max_tokens is None else max_tokens
        if self.api == "responses":
            return await self._responses(messages, tools, tool_choice, limit)
        messages = [{k: v for k, v in m.items() if k != RAW_KEY} for m in messages]
        for _ in range(3):
            body: dict[str, Any] = {"model": self.model, "messages": messages}
            if self._send_temperature:
                body["temperature"] = self.temperature
            if limit > 0:
                body[self._token_param] = limit
            if tools:
                body["tools"] = tools
                body["tool_choice"] = tool_choice or "auto"
                # Qwen Cloud: 생각 모드에서는 특정 도구 강제를 받지 않는다 → 강제할 때만 생각을 끈다 (D-73,
                # https://docs.qwencloud.com/developer-guides/text-generation/function-calling)
                if self.provider == "qwen" and isinstance(tool_choice, dict):
                    body["enable_thinking"] = False
            r = await self.http.request(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
            )
            if r.status_code != 400 or not self._adapt(r):
                break
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
        usage, cost = parse_chat_usage(data.get("usage"))
        return ChatResult(
            content=strip_reasoning(msg.get("content") or ""),
            tool_calls=calls,
            usage=usage,
            finish_reason=str(choice.get("finish_reason", "")),
            cost=cost,
            meta=response_meta(data),
        )

    async def _responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
        limit: int,
    ) -> ChatResult:
        """OpenAI Responses API (POST /responses). 추론형 모델이 추론과 도구를 함께 쓰는 경로 (D-42)."""
        for _ in range(3):
            # store=false: 포트폴리오·매매 내역을 OpenAI 쪽 대화 저장소에 남기지 않는다(추론 항목은 암호화돼 돌아온다)
            body: dict[str, Any] = {
                "model": self.model,
                "input": to_responses_input(messages),
                "store": False,
            }
            if self._send_temperature:
                body["temperature"] = self.temperature
            if limit > 0:
                body["max_output_tokens"] = limit
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
            if tools:
                body["tools"] = to_responses_tools(tools)
                body["tool_choice"] = to_responses_tool_choice(tool_choice)
            r = await self.http.request(
                "POST",
                f"{self.base_url}/responses",
                json=body,
                headers=self._headers(),
            )
            if r.status_code != 400 or not self._adapt(r):
                break
        if r.status_code != 200:
            text = r.text[:600]
            if tools and r.status_code in (400, 404, 422) and re.search(r"tool|function", text, re.I):
                raise ToolsUnsupportedError(f"tool calling 미지원 응답({r.status_code}): {text[:200]}")
            raise LLMError(f"LLM HTTP {r.status_code}: {text[:300]}")
        try:
            data = r.json()
        except ValueError as e:
            raise LLMError(f"LLM 응답 형식 오류: {r.text[:200]}") from e
        if data.get("status") == "failed":
            raise LLMError(f"LLM 응답 실패: {str(data.get('error'))[:300]}")
        return parse_responses(data)

    @property
    def provider(self) -> str:
        """요금을 매기는 곳 (D-66): openai | google | openrouter (주소로 판별),
        그 밖의 주소는 무료 묶음이면 local(자체 서버 — API 요금 없음), 유료 묶음이면 gateway(단가표로 계산)."""
        known = providers.detect(self.base_url, "")
        if known:
            return known
        return "gateway" if self.profile == "paid" else "local"

    def _headers(self) -> dict[str, str]:
        """키가 없으면(키를 받지 않는 자체 서버) Authorization 을 보내지 않는다."""
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _adapt(self, r: httpx.Response) -> bool:
        """400 'unsupported parameter/value' 응답이면 매개변수를 바꾸고 True (다시 보낸다).

        Chat Completions 는 `code: unsupported_parameter|unsupported_value` 를 주지만, Responses API 는
        `code: null` 에 메시지만 "Unsupported parameter: 'temperature' ..." 로 준다(2026-11-04 실제 응답). 둘 다 본다.
        """
        try:
            err = (r.json() or {}).get("error") or {}
        except ValueError:
            return False
        if not isinstance(err, dict):
            return False
        param, code = str(err.get("param") or ""), str(err.get("code") or "")
        message = str(err.get("message") or "")
        unsupported = code in ("unsupported_parameter", "unsupported_value") or bool(
            re.match(r"\s*unsupported (parameter|value)", message, re.I)
        )
        if not unsupported:
            return False
        if param == "max_tokens" and self._token_param == "max_tokens":
            self._token_param = "max_completion_tokens"
        elif param == "temperature" and self._send_temperature:
            self._send_temperature = False
        elif param.startswith("reasoning") and self.reasoning_effort:
            # 고른 모델이 .env 의 추론 강도를 받지 않으면 모델 기본값으로
            log.warning("LLM 이 추론 강도 %r 를 받지 않아 모델 기본값으로 보냄", self.reasoning_effort)
            self.reasoning_effort = ""
        else:
            return False
        log.info("LLM 매개변수 조정: %s 거절 → 다시 보냄", param)
        return True


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
