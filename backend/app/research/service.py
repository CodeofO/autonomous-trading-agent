"""리서치 퍼사드. 키가 있으면 실데이터, 없으면 MockWorld 를 쓴다.

에이전트 tool 은 이 클래스만 안다.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.clock import Clock
from app.market.mock_world import MockWorld
from app.research.dart import DartClient
from app.research.naver import NaverNewsClient

log = logging.getLogger(__name__)


class ResearchService:
    def __init__(
        self,
        clock: Clock,
        *,
        dart: DartClient | None = None,
        news: NaverNewsClient | None = None,
        world: MockWorld | None = None,
    ) -> None:
        self.clock = clock
        self.dart = dart
        self.news_client = news
        self.world = world

    @property
    def sources(self) -> dict[str, str]:
        return {
            "financials": "DART" if self.dart else ("MOCK" if self.world else "없음"),
            "news": "NAVER" if self.news_client else ("MOCK" if self.world else "없음"),
        }

    def _today(self) -> date:
        return self.clock.now().date()

    async def financials(self, symbol: str) -> dict[str, Any]:
        if self.dart:
            return await self.dart.financials(symbol, self._today())
        if self.world:
            return self.world.financials(symbol, self.clock.now())
        return {"symbol": symbol, "available": False, "reason": "DART_API_KEY 미설정"}

    async def disclosures(self, symbol: str, days: int = 30) -> list[dict[str, Any]]:
        if self.dart:
            return await self.dart.disclosures(symbol, self._today(), days)
        if self.world:
            return self.world.disclosures(symbol, self.clock.now(), days)
        return []

    async def news(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self.news_client:
            return await self.news_client.search(query, display=limit, sort="date")
        if self.world:
            return self.world.news(query, self.clock.now(), limit)
        return []
