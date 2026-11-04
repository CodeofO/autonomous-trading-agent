"""구글 검색 (Gemini Google 검색 그라운딩, D-49): 요청 형식·기간 필터·출처 파싱·세션당 한도·오류 (실제 호출 없음)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.http import SafeHttpClient
from app.research import google
from app.research.google import GoogleSearchClient, GoogleSearchError

URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
NOW = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)
OK = httpx.Response(
    200,
    json={
        "candidates": [
            {
                "content": {"parts": [{"text": "- 11/04 — Reuters — 엔비디아 실적 기대"}]},
                "groundingMetadata": {
                    "webSearchQueries": ["NVDA stock news"],
                    "groundingChunks": [
                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/a", "title": "reuters.com"}},
                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/a", "title": "dup"}},
                        {"web": {"uri": "https://vertexaisearch.cloud.google.com/b", "title": "cnbc.com"}},
                    ],
                },
            }
        ]
    },
)


def _client(**kw: object) -> GoogleSearchClient:
    return GoogleSearchClient(
        " key-test ",
        "gemini-test",
        http=SafeHttpClient(google.ROUTES, rate_per_sec=1000, backoff_scale=0.0),
        **kw,  # type: ignore[arg-type]
    )


@respx.mock
async def test_request_shape_time_filter_and_parsing() -> None:
    route = respx.post(URL).mock(return_value=OK)
    out = await _client().search("NVDA 최근 뉴스", NOW, days=1, limit=5)
    req = route.calls[0].request
    assert req.headers["x-goog-api-key"] == "key-test" and "key" not in str(req.url)
    body = json.loads(req.content)
    flt = body["tools"][0]["googleSearch"]["timeRangeFilter"]
    assert flt == {"startTime": "2026-11-03T14:01:00Z", "endTime": "2026-11-04T14:01:00Z"}
    assert "NVDA 최근 뉴스" in body["contents"][0]["parts"][0]["text"]
    assert out["answer"].startswith("- 11/04") and out["searches"] == ["NVDA stock news"]
    assert [s["title"] for s in out["sources"]] == ["reuters.com", "cnbc.com"]  # 같은 주소는 한 번만
    assert out["grounded"] is True and out["days"] == 1


@respx.mock
async def test_session_limit_and_errors() -> None:
    respx.post(URL).mock(return_value=OK)
    c = _client(max_per_session=2)
    await c.search("a", NOW, run_id=7)
    await c.search("b", NOW, run_id=7)
    with pytest.raises(GoogleSearchError, match="한도"):
        await c.search("c", NOW, run_id=7)
    assert c.remaining(8) == 2  # 다른 세션은 따로 센다

    respx.post(URL).mock(
        return_value=httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}})
    )
    with pytest.raises(GoogleSearchError, match="429 RESOURCE_EXHAUSTED"):
        await _client().search("d", NOW)


def test_empty_answer_without_sources() -> None:
    out = google.parse({"candidates": [{"content": {"parts": [{"text": "관련 뉴스 없음"}]}}]}, "q", 7)
    assert out == {
        "query": "q",
        "days": 7,
        "answer": "관련 뉴스 없음",
        "sources": [],
        "searches": [],
        "grounded": False,
    }
