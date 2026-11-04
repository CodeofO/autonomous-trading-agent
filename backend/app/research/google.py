"""구글 검색 — Gemini API 'Grounding with Google Search' (D-49).

구글의 일반 웹 검색 API(Custom Search JSON API)는 신규 가입이 막혔고 2027-01-01 종료 예정이라
(https://developers.google.com/custom-search/v1/overview), 공식적으로 남은 방법인 Gemini 의 Google 검색 도구를 쓴다.

- 요청: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
        헤더 x-goog-api-key, 본문 tools: [{"googleSearch": {"timeRangeFilter": {startTime, endTime}}}]
        (https://aistudio.google.com/docs/google-search, timeRangeFilter: API reference 의 GoogleSearch.timeRangeFilter —
         startTime 포함·endTime 미포함, 둘 다 넣어야 함)
- 응답: candidates[0].content.parts[].text (모델이 정리한 답),
        candidates[0].groundingMetadata.groundingChunks[].web.{uri,title}, webSearchQueries
- 요금: 유료 등급에서 모델이 실행한 검색 1회마다 과금(Gemini 3+ 월 5,000회 무료 후 1,000회당 $14, 2026-11-04 기준
        https://aistudio.google.com/docs/pricing) + 토큰 요금. 무료 등급에는 검색 도구가 없다 → 결제 등록 필요.
  그래서 세션당 호출 횟수를 제한한다(max_per_session).
- 검색 결과를 사용자 화면에 그대로 보여 줄 때는 Search Suggestions(searchEntryPoint)를 함께 표시해야 한다는 약관이 있어,
  결과는 매니저에게만 넘기고 화면에는 검색어·건수만 보인다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app.core.http import Route, SafeHttpClient

HOST = "generativelanguage.googleapis.com"
ROUTES = [Route(HOST, "POST", re.compile(r"/v1beta/models/[A-Za-z0-9._-]+:generateContent"))]
log = logging.getLogger(__name__)

PROMPT = (
    "Use Google Search to find the most recent news and analysis for: {query}\n"
    "Search at most once or twice. Only use results published within the time window.\n"
    "Answer in Korean as a bullet list of up to {limit} items: '날짜 — 출처 — 핵심 한 줄'. "
    "If nothing relevant is found, say so in one line."
)


class GoogleSearchError(RuntimeError):
    pass


class GoogleSearchClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_per_session: int = 3,
        http: SafeHttpClient | None = None,
    ) -> None:
        self._key = api_key.strip()
        self.model = model.strip()
        self.max_per_session = max_per_session
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=2, timeout=60, max_retries=1)
        self._used: dict[int | None, int] = {}  # 세션(run_id)별 호출 수

    def remaining(self, run_id: int | None) -> int:
        return max(0, self.max_per_session - self._used.get(run_id, 0))

    async def search(
        self, query: str, now: datetime, *, days: int = 1, limit: int = 6, run_id: int | None = None
    ) -> dict[str, Any]:
        if self.remaining(run_id) <= 0:
            raise GoogleSearchError(f"이번 세션 구글 검색 한도({self.max_per_session}회)를 다 썼어요")
        days = max(1, min(30, int(days)))
        end = now + timedelta(minutes=1)
        start = end - timedelta(days=days)
        body = {
            "contents": [{"role": "user", "parts": [{"text": PROMPT.format(query=query, limit=limit)}]}],
            "tools": [
                {
                    "googleSearch": {
                        "timeRangeFilter": {
                            "startTime": start.isoformat().replace("+00:00", "Z"),
                            "endTime": end.isoformat().replace("+00:00", "Z"),
                        }
                    }
                }
            ],
        }
        self._used[run_id] = self._used.get(run_id, 0) + 1
        r = await self.http.request(
            "POST",
            f"https://{HOST}/v1beta/models/{self.model}:generateContent",
            json=body,
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            try:
                err = (r.json() or {}).get("error") or {}
            except ValueError:
                err = {}
            status = err.get("status", "") if isinstance(err, dict) else ""
            raise GoogleSearchError(f"구글 검색 HTTP {r.status_code} {status}".strip())
        return parse(r.json(), query, days)


def parse(data: dict[str, Any], query: str, days: int) -> dict[str, Any]:
    cands = data.get("candidates") or []
    cand = cands[0] if cands else {}
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    meta = cand.get("groundingMetadata") or {}
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for ch in meta.get("groundingChunks") or []:
        web = (ch or {}).get("web") or {}
        uri = str(web.get("uri") or "")
        if uri and uri not in seen:
            seen.add(uri)
            sources.append({"title": str(web.get("title") or ""), "uri": uri})
    return {
        "query": query,
        "days": days,
        "answer": text[:3000],
        "sources": sources[:10],
        "searches": list(meta.get("webSearchQueries") or []),
        "grounded": bool(sources),
    }
