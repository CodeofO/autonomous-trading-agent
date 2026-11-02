"""네이버 검색 API(뉴스) 클라이언트 (docs/DATA_API_NOTES.md §3)."""

from __future__ import annotations

import html
import re
from typing import Any

from app.core.http import Route, SafeHttpClient

HOST = "openapi.naver.com"
URL = f"https://{HOST}/v1/search/news.json"
ROUTES = [Route(HOST, "GET", re.compile(r"/v1/search/news\.json"))]
_TAG = re.compile(r"<[^>]+>")


class NaverError(RuntimeError):
    pass


def clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


class NaverNewsClient:
    def __init__(self, client_id: str, client_secret: str, http: SafeHttpClient | None = None) -> None:
        self._headers = {
            "X-Naver-Client-Id": client_id.strip(),
            "X-Naver-Client-Secret": client_secret.strip(),
        }
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=8)

    async def search(self, query: str, display: int = 10, sort: str = "date") -> list[dict[str, Any]]:
        display = max(1, min(100, display))
        if sort not in ("date", "sim"):
            sort = "date"
        r = await self.http.request(
            "GET",
            URL,
            params={"query": query, "display": display, "start": 1, "sort": sort},
            headers=self._headers,
        )
        if r.status_code != 200:
            try:
                err = r.json()
            except ValueError:
                err = {}
            raise NaverError(f"네이버 뉴스 HTTP {r.status_code} {err.get('errorCode', '')}")
        return [
            {
                "title": clean(it.get("title", "")),
                "description": clean(it.get("description", "")),
                "link": it.get("originallink") or it.get("link", ""),
                "pubDate": it.get("pubDate", ""),
                "source": "NAVER",
            }
            for it in r.json().get("items", [])
        ]
