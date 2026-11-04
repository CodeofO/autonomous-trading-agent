"""LLM 연결 점검 (D-42): 코드를 고친 뒤 실제 호출로 요청 형식·도구 호출을 확인한다.

    python -m app.agent.llm_check          # 테스트용 엔드포인트(LLM_TEST_*)
    python -m app.agent.llm_check --live   # 실서비스 엔드포인트(LLM_*) — 실제 과금

짧은 요청 4개(일반 답·도구 호출·도구 강제 호출·도구 결과 되돌려 받기)만 보낸다. 키는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from app.agent.llm import RAW_KEY, LLMClient
from app.core.config import Settings, get_settings
from app.core.logging import setup_logging

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": "종목 현재가 조회",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "description": "종목 코드"}},
                "required": ["symbol"],
            },
        },
    }
]
ASK = [
    {"role": "user", "content": "삼성전자(005930) 현재가를 get_price 도구로 조회한 뒤 한 문장으로 알려 줘."}
]


def client(s: Settings, live: bool) -> LLMClient | None:
    if live:
        if not s.has_llm:
            return None
        assert s.llm_api_key is not None
        return LLMClient(
            s.llm_paid_base_url,
            s.llm_model,
            s.llm_api_key.get_secret_value(),
            temperature=s.llm_temperature,
            max_tokens=2048,
            timeout=90,
            api=s.llm_api,
            reasoning_effort=s.llm_reasoning_effort,
        )
    if not s.has_test_llm:
        return None
    assert s.llm_test_api_key is not None
    return LLMClient(
        s.llm_test_base_url.strip(),
        s.llm_test_model.strip(),
        s.llm_test_api_key.get_secret_value(),
        temperature=s.llm_temperature,
        max_tokens=2048,
        timeout=90,
        api=s.llm_test_api,
        reasoning_effort=s.llm_test_reasoning_effort,
    )


def _usage(u: dict[str, int]) -> str:
    keys = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens")
    return " ".join(f"{k.split('_')[0]}={u[k]}" for k in keys if u.get(k))


async def check(c: LLMClient, results: list[dict[str, Any]] | None = None, quiet: bool = False) -> bool:
    """짧은 요청 4개. results 를 주면 단계별 결과({label, ok, detail})를 담는다(설정 화면 '연결 확인')."""
    ok = True
    out = results if results is not None else []

    def say(line: str) -> None:
        if not quiet:
            print(line)

    say(f"대상: {c.model} · {c.api} API")

    async def step(label: str, coro: Any) -> Any:
        nonlocal ok
        try:
            res = await coro
            calls = [(t.name, t.arguments) for t in res.tool_calls]
            detail = f"finish={res.finish_reason} tools={calls} text={res.content[:50]!r} {_usage(res.usage)}"
            say(f"  ✅ {label}: {detail}")
            out.append({"label": label, "ok": True, "detail": detail.strip()})
            return res
        except Exception as e:
            ok = False
            detail = f"{type(e).__name__}: {str(e)[:300]}"
            say(f"  ❌ {label}: {detail}")
            out.append({"label": label, "ok": False, "detail": detail})
            return None

    await step("일반 답", c.chat([{"role": "user", "content": "한 단어로만 답해: 하늘은 무슨 색?"}]))
    first = await step("도구 호출", c.chat(ASK, TOOLS))
    await step("도구 강제 호출", c.chat(ASK, TOOLS, {"type": "function", "function": {"name": "get_price"}}))
    if first is not None and first.tool_calls:
        call = first.tool_calls[0]
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": first.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
            ],
        }
        if first.raw_output:
            assistant[RAW_KEY] = first.raw_output
        follow = [
            *ASK,
            assistant,
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps({"symbol": "005930", "price": 70000}),
            },
        ]
        await step("도구 결과 되돌려 받기", c.chat(follow, TOOLS))
    elif first is not None:
        ok = False
        say("  ❌ 도구 결과 되돌려 받기: 모델이 도구를 부르지 않아 확인하지 못함")
        out.append(
            {
                "label": "도구 결과 되돌려 받기",
                "ok": False,
                "detail": "모델이 도구를 부르지 않아 확인하지 못함",
            }
        )
    say(
        f"  매개변수: 토큰 한도={c._token_param if c.api == 'chat' else 'max_output_tokens'} temperature={'보냄' if c._send_temperature else '안 보냄'}"
    )
    await c.http.aclose()
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LLM 연결 점검 (기본: 테스트용 엔드포인트)")
    ap.add_argument("--live", action="store_true", help="실서비스 LLM_* 로 점검 (실제 과금)")
    args = ap.parse_args(argv)
    s = get_settings()
    setup_logging("WARNING", s.secret_values())
    c = client(s, args.live)
    if c is None:
        which = (
            "LLM_BASE_URL / LLM_MODEL / LLM_API_KEY"
            if args.live
            else "LLM_TEST_BASE_URL / LLM_TEST_MODEL / LLM_TEST_API_KEY"
        )
        print(f".env 에 {which} 를 채운 뒤 다시 실행하세요.")
        return 2
    return 0 if asyncio.run(check(c)) else 1


if __name__ == "__main__":
    sys.exit(main())
