"""네이버 뉴스 검색 클라이언트 (docs/DATA_API_NOTES.md §3).

키를 받는 곳이 두 군데라 주소·헤더가 다르다 (D-44, `NAVER_API`):
- hub        : 네이버 클라우드 NAVER API HUB (console.ncloud.com > NAVER API HUB > Application)
               GET https://naverapihub.apigw.ntruss.com/search/v1/news  · X-NCP-APIGW-API-KEY-ID / -KEY
- developers : 네이버 개발자센터 (developers.naver.com > 내 애플리케이션)
               GET https://openapi.naver.com/v1/search/news.json        · X-Naver-Client-Id / -Secret
요청 파라미터(query·display·start·sort)와 응답(items[].title·originallink·link·description·pubDate)은 같다.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.http import Route, SafeHttpClient

NaverApi = Literal["hub", "developers"]

HUB_HOST = "naverapihub.apigw.ntruss.com"
HUB_URL = f"https://{HUB_HOST}/search/v1/news"
HOST = "openapi.naver.com"
URL = f"https://{HOST}/v1/search/news.json"
ROUTES = [
    Route(HUB_HOST, "GET", re.compile(r"/search/v1/news")),
    Route(HOST, "GET", re.compile(r"/v1/search/news\.json")),
]
_TAG = re.compile(r"<[^>]+>")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Endpoint:
    url: str
    id_header: str
    secret_header: str
    hint: str
    params: dict[str, str] = field(default_factory=dict)


ENDPOINTS: dict[str, Endpoint] = {
    "hub": Endpoint(
        HUB_URL,
        "X-NCP-APIGW-API-KEY-ID",
        "X-NCP-APIGW-API-KEY",
        "NAVER API HUB > Application > 인증 정보의 Client ID / Client Secret 을 .env 의 "
        "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 에 넣고, 그 Application 에 검색 API 가 등록돼 있는지 확인한 뒤 "
        "./run.sh up 으로 재시작하세요",
        {"format": "json"},
    ),
    "developers": Endpoint(
        URL,
        "X-Naver-Client-Id",
        "X-Naver-Client-Secret",
        "developers.naver.com > 내 애플리케이션의 Client ID / Client Secret 을 .env 의 "
        "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 에 넣고(사용 API 에 '검색' 포함) ./run.sh up 으로 재시작하세요. "
        "NAVER API HUB(네이버 클라우드) 키라면 NAVER_API=hub",
    ),
}
LABEL = {"hub": "NAVER API HUB", "developers": "네이버 개발자센터"}


class NaverError(RuntimeError):
    pass


def clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def _error_code(r: Any) -> str:
    """검색 API 오류 {errorCode, errorMessage} 와 API 게이트웨이 오류 {error: {errorCode, message}} 둘 다."""
    try:
        body = r.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    inner = body.get("error")
    if isinstance(inner, dict):
        return str(inner.get("errorCode", ""))
    return str(body.get("errorCode", ""))


class NaverNewsClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: SafeHttpClient | None = None,
        api: NaverApi = "hub",
    ) -> None:
        self.api = api
        self.endpoint = ENDPOINTS[api]
        self._headers = {
            self.endpoint.id_header: client_id.strip(),
            self.endpoint.secret_header: client_secret.strip(),
        }
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=8)
        # 키 인증 실패(401)는 재시작 전까지 다시 시도해도 같다 → 기억해 두고 바로 실패, 화면에도 표시
        self.auth_error: str | None = None

    async def search(self, query: str, display: int = 10, sort: str = "date") -> list[dict[str, Any]]:
        if self.auth_error:
            raise NaverError(self.auth_error)
        display = max(1, min(100, display))
        if sort not in ("date", "sim"):
            sort = "date"
        r = await self.http.request(
            "GET",
            self.endpoint.url,
            params={"query": query, "display": display, "start": 1, "sort": sort, **self.endpoint.params},
            headers=self._headers,
        )
        if r.status_code != 200:
            code = _error_code(r)
            if r.status_code == 401:
                self.auth_error = f"네이버 키 인증 실패(HTTP 401 {code}) — {self.endpoint.hint}"
                log.warning(
                    "네이버 검색 키 인증 실패(%s, HTTP 401 %s) — 뉴스 검색을 멈춥니다", self.api, code
                )
                raise NaverError(self.auth_error)
            if r.status_code == 429:
                raise NaverError(f"네이버 검색 하루 호출 한도 초과(HTTP 429 {code})")
            raise NaverError(f"네이버 뉴스 HTTP {r.status_code} {code}")
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
