"""외부 HTTP 호출의 단일 출구.

- (호스트, 메서드, 경로) **허용목록** 에 있는 요청만 네트워크로 나간다. 나머지는 전송 전에 예외.
- 증권사 주문 API 호스트는 명시적 차단목록으로도 막는다(이중 방어, D-02).
- 토큰 버킷(초당 한도) + 429/5xx 지수 백오프 재시도.
- 로그에는 쿼리스트링(키 포함)을 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)


class OutboundBlockedError(RuntimeError):
    """허용목록에 없는 외부 호출."""


class LiveOrderBlockedError(OutboundBlockedError):
    """실거래(증권사) 주문 엔드포인트 호출 시도."""


# 실계좌 주문이 가능한 증권사 API 호스트 — 어떤 경로든 차단
BROKER_HOST_DENYLIST: tuple[str, ...] = (
    "openapi.tossinvest.com",
    "openapi-ws.tossinvest.com",
    "openapi.koreainvestment.com",
    "openapivts.koreainvestment.com",
    "api.kiwoom.com",
    "mockapi.kiwoom.com",
    "openapi.ls-sec.co.kr",
    "api.alpaca.markets",
    "paper-api.alpaca.markets",
)
_ORDER_PATH = re.compile(r"/(orders?|trading|conditional-orders?|order-cash|order-rvsecncl)(/|$|\?)", re.I)


@dataclass(frozen=True)
class Route:
    host: str
    method: str
    path: re.Pattern[str]


@dataclass
class TokenBucket:
    rate: float  # 초당 토큰
    capacity: float
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last = time.monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


def check_outbound(method: str, url: str, routes: list[Route]) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in BROKER_HOST_DENYLIST):
        raise LiveOrderBlockedError(f"증권사 API 호출 차단: {method} {host}{parts.path}")
    for r in routes:
        if r.host == host and r.method == method.upper() and r.path.fullmatch(parts.path):
            return
    if _ORDER_PATH.search(parts.path):
        raise LiveOrderBlockedError(f"주문 엔드포인트로 보이는 호출 차단: {method} {host}{parts.path}")
    raise OutboundBlockedError(f"허용목록에 없는 외부 호출 차단: {method} {host}{parts.path}")


class SafeHttpClient:
    """허용목록 + 레이트리밋 + 재시도가 적용된 httpx 래퍼."""

    def __init__(
        self,
        routes: list[Route],
        *,
        rate_per_sec: float = 5.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_scale: float = 1.0,
    ) -> None:
        self.routes = routes
        self.backoff_scale = backoff_scale
        self.bucket = TokenBucket(rate=rate_per_sec, capacity=max(1.0, rate_per_sec))
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        check_outbound(method, url, self.routes)
        attempt = 0
        while True:
            await self.bucket.acquire()
            try:
                resp = await self._client.request(method, url, params=params, headers=headers, json=json)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(self._backoff(attempt))
                attempt += 1
                log.warning("외부 호출 재시도 %s %s (%s)", method, urlsplit(url).path, type(e).__name__)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    return resp
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after) * self.backoff_scale
                    if retry_after and retry_after.isdigit()
                    else self._backoff(attempt)
                )
                log.warning("외부 호출 %s 응답, %.1fs 후 재시도: %s", resp.status_code, delay, urlsplit(url).path)
                await asyncio.sleep(delay)
                attempt += 1
                continue
            return resp

    def _backoff(self, attempt: int) -> float:
        return float(min(30.0, 2**attempt) + random.uniform(0, 0.5)) * self.backoff_scale

    async def aclose(self) -> None:
        await self._client.aclose()
