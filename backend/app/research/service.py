"""리서치 퍼사드 — 재무제표·공시(OpenDART), 뉴스(네이버), 구글 웹 검색(Gemini 그라운딩, 유료·선택, D-49).
토스 API 에는 없는 정보만 담당한다.

키가 없으면 "데이터 없음"을 돌려주고, 에이전트는 토스 시세·수급만으로 판단한다.
"""

from __future__ import annotations

from datetime import UTC, date
from typing import Any

from app.core.clock import KST, Clock
from app.research.dart import DartClient
from app.research.google import GoogleSearchClient
from app.research.naver import NaverNewsClient


class ResearchService:
    def __init__(
        self,
        clock: Clock,
        *,
        dart: DartClient | None = None,
        news: NaverNewsClient | None = None,
        google: GoogleSearchClient | None = None,
    ) -> None:
        self.clock = clock
        self.dart = dart
        self.news_client = news
        self.google = google  # 스택마다 따로 (세션당 검색 한도를 run_id 로 세므로 국장·미장이 섞이지 않게)

    @property
    def sources(self) -> dict[str, str]:
        return {
            "financials": "DART" if self.dart else "없음",
            "news": "NAVER" if self.news_client else "없음",
            "web": "GOOGLE" if self.google else "없음",
        }

    @property
    def news_error(self) -> str | None:
        """뉴스 키 인증 실패 사유(재시작 전까지 유지). 화면에 '키 인증 실패'로 표시한다."""
        return self.news_client.auth_error if self.news_client else None

    def _today(self) -> date:
        return self.clock.now().astimezone(KST).date()

    async def financials(self, symbol: str) -> dict[str, Any]:
        if self.dart:
            return await self.dart.financials(symbol, self._today())
        return {"symbol": symbol, "available": False, "reason": "DART_API_KEY 미설정 — 재무제표 없음"}

    async def disclosures(self, symbol: str, days: int = 30) -> list[dict[str, Any]]:
        return await self.dart.disclosures(symbol, self._today(), days) if self.dart else []

    async def news(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self.news_client.search(query, display=limit, sort="date") if self.news_client else []

    async def web_search(
        self, query: str, *, days: int = 1, limit: int = 6, run_id: int | None = None
    ) -> dict[str, Any]:
        """구글 검색 (유료, 세션당 한도). 키가 없으면 호출하지 않는다."""
        if self.google is None:
            return {"query": query, "available": False, "reason": "GEMINI_API_KEY 미설정 — 구글 검색 없음"}
        now = self.clock.now().astimezone(UTC)
        return await self.google.search(query, now, days=days, limit=limit, run_id=run_id)
