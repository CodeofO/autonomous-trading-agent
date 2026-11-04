"""토스증권 Open API — **조회 전용** 클라이언트와 시세 공급자.

공식 문서: docs/TOSS_API_NOTES.md (OpenAPI 1.2.28)
- 허용 경로: 토큰 발급, 현재가, 캔들, 종목 정보, 전체 종목, 랭킹, 국내·미국 장 운영 캘린더 (조회만)
- 주문·계좌 경로는 `core/http.check_outbound` 가 항상 차단한다 (이 모듈에는 주문 코드가 없다)
- 토큰은 클라이언트당 1개만 유효 → 메모리에 두고 재사용, 만료 60초 전 또는 401 수신 시에만 재발급
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.clock import KST, Clock
from app.core.http import Route, SafeHttpClient, TokenBucket
from app.core.logging import register_secret
from app.market.types import Bar

log = logging.getLogger(__name__)

HOST = "openapi.tossinvest.com"
BASE = f"https://{HOST}"

ROUTES = [
    Route(HOST, "POST", re.compile(r"/oauth2/token")),
    Route(HOST, "GET", re.compile(r"/api/v1/prices")),
    Route(HOST, "GET", re.compile(r"/api/v1/candles")),
    Route(HOST, "GET", re.compile(r"/api/v1/stocks")),
    Route(HOST, "GET", re.compile(r"/api/v1/stocks/all")),
    Route(HOST, "GET", re.compile(r"/api/v1/rankings")),
    Route(HOST, "GET", re.compile(r"/api/v1/market-calendar/(KR|US)")),
    Route(HOST, "GET", re.compile(r"/api/v1/orderbook")),
    Route(HOST, "GET", re.compile(r"/api/v1/trades")),
    Route(HOST, "GET", re.compile(r"/api/v1/price-limits")),
    Route(
        HOST,
        "GET",
        re.compile(
            r"/api/v1/stocks/[A-Za-z0-9.\-]+/(warnings|investor-trading|program-trades|short-selling|credit-trades|securities-lending)"
        ),
    ),
    Route(HOST, "GET", re.compile(r"/api/v1/exchange-rate")),
    Route(HOST, "GET", re.compile(r"/api/v1/market-indicators/prices")),
    Route(HOST, "GET", re.compile(r"/api/v1/market-indicators/(KOSPI|KOSDAQ)/(candles|investor-trading)")),
    # 국채 금리는 일봉만 (공식 심볼 카탈로그: KR_BOND_2Y·3Y·5Y·10Y·20Y·30Y)
    Route(HOST, "GET", re.compile(r"/api/v1/market-indicators/KR_BOND_(2Y|3Y|5Y|10Y|20Y|30Y)/candles")),
    Route(HOST, "GET", re.compile(r"/api/v1/sectors")),
    Route(HOST, "GET", re.compile(r"/api/v1/sectors/rankings")),
    Route(HOST, "GET", re.compile(r"/api/v1/sectors/[0-9A-Za-z\-]+(/stocks)?")),
    Route(HOST, "GET", re.compile(r"/api/v1/rankings/investor-trading")),
]

# 공식 Rate Limits 의 80% (docs/TOSS_API_NOTES.md §7)
RATE_GROUPS: dict[str, float] = {
    "AUTH": 4,
    "MARKET_DATA": 12,
    "MARKET_DATA_CHART": 16,
    "STOCK": 4,
    "STOCK_ALL": 0.8,
    "RANKING": 4,
    "MARKET_INFO": 2.4,
    "STOCK_TRADING_TREND": 8,
    "MARKET_INDICATOR": 8,
    "MARKET_INDICATOR_CHART": 4,
    "SECTOR": 4,
    "SECTOR_RANKING": 4,
}
KR_MARKETS = ("KOSPI", "KOSDAQ")


class TossApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"토스 API {status} {code}: {message}")
        self.status = status
        self.code = code


def _num(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


class TossClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: SafeHttpClient | None = None,
        rate_scale: float = 1.0,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=40, max_retries=4)
        self._buckets = {
            g: TokenBucket(rate=r * rate_scale, capacity=max(1.0, r)) for g, r in RATE_GROUPS.items()
        }
        self._token: str | None = None
        self._token_exp = 0.0
        self._token_lock = asyncio.Lock()

    # ------------------------------------------------------------ 인증
    async def _issue_token(self) -> str:
        await self._buckets["AUTH"].acquire()
        r = await self.http.request(
            "POST",
            f"{BASE}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        if r.status_code != 200:
            try:
                body = r.json()
            except ValueError:
                body = {}
            # OAuth2 형식 오류(invalid_client, access_denied 등)
            raise TossApiError(
                r.status_code, str(body.get("error", "")), str(body.get("error_description", ""))[:200]
            )
        data = r.json()
        token = str(data["access_token"])
        register_secret(token)
        self._token = token
        self._token_exp = time.monotonic() + float(data.get("expires_in", 86400))
        log.info("토스 액세스 토큰 발급 (만료 %ss)", data.get("expires_in"))
        return token

    async def _bearer(self, force: bool = False) -> str:
        async with self._token_lock:
            if force or self._token is None or time.monotonic() > self._token_exp - 60:
                return await self._issue_token()
            return self._token

    async def _get(self, group: str, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in (0, 1):
            token = await self._bearer(force=attempt == 1)
            await self._buckets[group].acquire()
            r = await self.http.request(
                "GET", f"{BASE}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
            )
            if r.status_code == 200:
                return r.json().get("result")
            code, message = "", r.text[:200]
            try:
                err = r.json().get("error") or {}
                code, message = str(err.get("code", "")), str(err.get("message", ""))
            except ValueError:
                pass
            if (
                r.status_code == 401
                and attempt == 0
                and code in ("invalid-token", "expired-token", "token-revoked")
            ):
                log.warning("토스 토큰 재발급 (%s)", code)
                continue
            raise TossApiError(r.status_code, code, message)
        raise TossApiError(401, "token", "토큰 재발급 후에도 인증 실패")

    # ------------------------------------------------------------ 조회
    async def prices(self, symbols: list[str]) -> dict[str, tuple[float, datetime | None]]:
        out: dict[str, tuple[float, datetime | None]] = {}
        uniq = list(dict.fromkeys(s for s in symbols if s))
        for i in range(0, len(uniq), 200):
            rows = await self._get("MARKET_DATA", "/api/v1/prices", {"symbols": ",".join(uniq[i : i + 200])})
            for it in rows or []:
                ts = it.get("timestamp")
                out[str(it["symbol"])] = (
                    _num(it.get("lastPrice")),
                    datetime.fromisoformat(ts) if ts else None,
                )
        return out

    async def candles(
        self, symbol: str, interval: str = "1d", count: int = 200, before: datetime | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "count": max(1, min(200, count))}
        if before is not None:
            params["before"] = before.isoformat()  # httpx 가 '+' 를 %2B 로 인코딩
        res = await self._get("MARKET_DATA_CHART", "/api/v1/candles", params)
        return list((res or {}).get("candles") or []), (res or {}).get("nextBefore")

    async def stocks(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        uniq = list(dict.fromkeys(s for s in symbols if s))
        for i in range(0, len(uniq), 200):
            rows = await self._get("STOCK", "/api/v1/stocks", {"symbols": ",".join(uniq[i : i + 200])})
            for it in rows or []:
                out[str(it["symbol"])] = it
        return out

    async def stocks_all(self, market: str, security_type: str | None = "STOCK") -> list[dict[str, Any]]:
        """마켓별 전체 종목. security_type=None 이면 모든 유형(미장은 STOCK·ETF 를 한 번에 받아 걸러 쓴다)."""
        params = {"market": market, "status": "ACTIVE"}
        if security_type:
            params["securityType"] = security_type
        rows = await self._get("STOCK_ALL", "/api/v1/stocks/all", params)
        return list(rows or [])

    async def rankings(
        self, type_: str, duration: str, count: int = 100, market_country: str = "KR"
    ) -> list[dict[str, Any]]:
        res = await self._get(
            "RANKING",
            "/api/v1/rankings",
            {
                "type": type_,
                "marketCountry": market_country,
                "duration": duration,
                "excludeInvestmentCaution": "true",
                "count": max(1, min(100, count)),
            },
        )
        return list((res or {}).get("rankings") or [])

    async def market_calendar_kr(self, day: date | None = None) -> dict[str, Any]:
        params = {"date": day.isoformat()} if day else None
        return dict(await self._get("MARKET_INFO", "/api/v1/market-calendar/KR", params) or {})

    async def market_calendar_us(self, day: date | None = None) -> dict[str, Any]:
        """미국 장 운영 정보. `day` 는 미국 현지 날짜. 응답 시각은 모두 KST(+09:00)."""
        params = {"date": day.isoformat()} if day else None
        return dict(await self._get("MARKET_INFO", "/api/v1/market-calendar/US", params) or {})

    # ------------------------------------------------------------ 시세 부가 정보
    async def orderbook(self, symbol: str) -> dict[str, Any]:
        return dict(await self._get("MARKET_DATA", "/api/v1/orderbook", {"symbol": symbol}) or {})

    async def trades(self, symbol: str, count: int = 50) -> list[dict[str, Any]]:
        return list(
            await self._get(
                "MARKET_DATA", "/api/v1/trades", {"symbol": symbol, "count": max(1, min(50, count))}
            )
            or []
        )

    async def price_limits(self, symbol: str) -> dict[str, Any]:
        return dict(await self._get("MARKET_DATA", "/api/v1/price-limits", {"symbol": symbol}) or {})

    # ------------------------------------------------------------ 종목 수급·상태
    async def warnings(self, symbol: str) -> list[dict[str, Any]]:
        return list(await self._get("STOCK", f"/api/v1/stocks/{symbol}/warnings") or [])

    async def stock_trend(self, symbol: str, kind: str, count: int = 10) -> list[dict[str, Any]]:
        """kind: investor-trading | program-trades | short-selling | credit-trades | securities-lending"""
        if kind not in (
            "investor-trading",
            "program-trades",
            "short-selling",
            "credit-trades",
            "securities-lending",
        ):
            raise ValueError(kind)
        res = await self._get(
            "STOCK_TRADING_TREND", f"/api/v1/stocks/{symbol}/{kind}", {"count": max(1, min(30, count))}
        )
        return list((res or {}).get("records") or [])

    # ------------------------------------------------------------ 시장
    async def exchange_rate(self) -> dict[str, Any]:
        return dict(
            await self._get(
                "MARKET_INFO", "/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"}
            )
            or {}
        )

    async def index_prices(self, symbols: tuple[str, ...] = ("KOSPI", "KOSDAQ")) -> list[dict[str, Any]]:
        return list(
            await self._get(
                "MARKET_INDICATOR", "/api/v1/market-indicators/prices", {"symbols": ",".join(symbols)}
            )
            or []
        )

    async def index_candles(self, symbol: str, count: int = 60) -> list[dict[str, Any]]:
        res = await self._get(
            "MARKET_INDICATOR_CHART",
            f"/api/v1/market-indicators/{symbol}/candles",
            {"interval": "1d", "count": max(1, min(200, count))},
        )
        return list((res or {}).get("candles") or [])

    async def index_investor_trading(self, symbol: str, count: int = 5) -> list[dict[str, Any]]:
        res = await self._get(
            "MARKET_INDICATOR",
            f"/api/v1/market-indicators/{symbol}/investor-trading",
            {"interval": "1d", "count": max(1, min(30, count))},
        )
        return list((res or {}).get("records") or [])

    async def sectors(self) -> list[dict[str, Any]]:
        return list((await self._get("SECTOR", "/api/v1/sectors") or {}).get("sectors") or [])

    async def sector(self, sector_id: str, market_country: str = "KR") -> dict[str, Any]:
        return dict(
            await self._get("SECTOR", f"/api/v1/sectors/{sector_id}", {"marketCountry": market_country}) or {}
        )

    async def sector_stocks(self, sector_id: str, market_country: str = "KR") -> list[dict[str, Any]]:
        res = await self._get(
            "SECTOR", f"/api/v1/sectors/{sector_id}/stocks", {"marketCountry": market_country}
        )
        return list((res or {}).get("stocks") or [])

    async def sector_rankings(
        self, type_: str = "TOP_GAINERS", duration: str = "1d", market_country: str = "KR"
    ) -> list[dict[str, Any]]:
        res = await self._get(
            "SECTOR_RANKING",
            "/api/v1/sectors/rankings",
            {"type": type_, "marketCountry": market_country, "duration": duration},
        )
        return list((res or {}).get("rankings") or [])

    async def investor_rankings(self, count: int = 20) -> dict[str, Any]:
        return dict(
            await self._get(
                "RANKING", "/api/v1/rankings/investor-trading", {"count": max(1, min(100, count))}
            )
            or {}
        )

    async def aclose(self) -> None:
        await self.http.aclose()


class TossMarketProvider:
    """토스 시세를 MarketDataProvider + 실시간 현재가(QuoteSource) + 스크리너로 노출."""

    name = "toss"

    def __init__(
        self,
        client: TossClient,
        clock: Clock,
        *,
        country: str = "KR",
        markets: tuple[str, ...] = KR_MARKETS,
        security_types: tuple[str, ...] = ("STOCK",),
        tz: ZoneInfo = KST,
    ) -> None:
        """country=US 이면 미장(NASDAQ·NYSE·AMEX 의 주식·ETF, 랭킹 marketCountry=US, 일봉 날짜는 뉴욕 기준)."""
        self.client = client
        self.clock = clock
        self.country = country
        self.markets = markets
        self.security_types = security_types
        self.tz = tz
        self._info: dict[str, dict[str, Any]] = {}
        self._universe: dict[str, dict[str, Any]] = {}
        self._universe_at = 0.0
        self._quote_cache: dict[str, tuple[float, float]] = {}
        self._quote_ts: dict[str, datetime | None] = {}  # 토스가 준 시세 시각 (장외 체결 판단, D-57)

    # ------------------------------------------------------------ 종목 정보
    async def info(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        missing = [s for s in symbols if s not in self._info]
        if missing:
            try:
                self._info.update(await self.client.stocks(missing))
            except TossApiError as e:
                log.warning("종목 정보 조회 실패: %s", e)
        return {s: self._info[s] for s in symbols if s in self._info}

    async def universe(self) -> dict[str, dict[str, Any]]:
        """전체 종목 목록 (하루 1회 캐시). 국장: 보통주, 미장: 보통주·ETF."""
        if not self._universe or time.monotonic() - self._universe_at > 20 * 3600:
            uni: dict[str, dict[str, Any]] = {}
            for m in self.markets:
                if self.country == "KR":
                    rows = await self.client.stocks_all(m)
                else:  # 유형 필터 없이 한 번에 받아 주식·ETF 만 남긴다 (STOCK_ALL 1/s 한도 → 호출 수 최소화)
                    rows = [
                        it
                        for it in await self.client.stocks_all(m, None)
                        if str(it.get("securityType") or "STOCK") in self.security_types
                    ]
                for it in rows:
                    if it.get("isCommonShare", True):
                        uni[str(it["symbol"])] = {**it, "market": m}
            if uni:
                self._universe, self._universe_at = uni, time.monotonic()
        return self._universe

    # ------------------------------------------------------------ MarketDataProvider
    async def fetch_day(self, day: date) -> list[Bar]:
        return []  # 토스에는 '전 종목 일봉' 엔드포인트가 없다 → 종목별 fetch_history 사용

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        info = (await self.info([symbol])).get(symbol, {})
        shares = _num(info.get("sharesOutstanding"))
        bars: dict[date, Bar] = {}
        # 현지 자정(미장은 뉴욕 자정)을 KST 로 표기해 보낸다 (문서 예시 형식 +09:00)
        before: datetime | None = datetime.combine(
            end + timedelta(days=1), datetime.min.time(), self.tz
        ).astimezone(KST)
        for _ in range(20):  # 200봉 × 20 = 약 16년치 상한
            rows, next_before = await self.client.candles(symbol, "1d", 200, before)
            for c in rows:
                d = datetime.fromisoformat(c["timestamp"]).astimezone(self.tz).date()
                if start <= d <= end:
                    close = _num(c.get("closePrice"))
                    bars[d] = Bar(
                        symbol=symbol,
                        day=d,
                        open=_num(c.get("openPrice")),
                        high=_num(c.get("highPrice")),
                        low=_num(c.get("lowPrice")),
                        close=close,
                        volume=_num(c.get("volume")),
                        value=close * _num(c.get("volume")),
                        market_cap=close * shares,
                        listed_shares=shares,
                        name=str(info.get("name", "")),
                        market=str(info.get("market", "")),
                    )
            oldest = min(
                (datetime.fromisoformat(c["timestamp"]).astimezone(self.tz).date() for c in rows),
                default=None,
            )
            if not next_before or oldest is None or oldest <= start:
                break
            before = datetime.fromisoformat(next_before)
        return sorted(bars.values(), key=lambda b: b.day)

    # ------------------------------------------------------------ QuoteSource
    async def last_prices(self, symbols: list[str], max_age: float = 5.0) -> dict[str, float]:
        now = time.monotonic()
        need = [s for s in symbols if s not in self._quote_cache or now - self._quote_cache[s][1] > max_age]
        if need:
            for sym, (px, ts) in (await self.client.prices(need)).items():
                if px > 0:
                    self._quote_cache[sym] = (px, now)
                    self._quote_ts[sym] = ts
        return {s: self._quote_cache[s][0] for s in symbols if s in self._quote_cache}

    async def last_quotes(
        self, symbols: list[str], max_age: float = 5.0
    ) -> dict[str, tuple[float, datetime | None]]:
        """현재가와 그 시세의 시각(토스 `timestamp`, 체결이 없으면 None)."""
        prices = await self.last_prices(symbols, max_age)
        return {s: (px, self._quote_ts.get(s)) for s, px in prices.items()}

    # ------------------------------------------------------------ 스크리너
    async def screen(
        self, sort_by: str, market: str, limit: int, min_market_cap: float = 0.0
    ) -> list[dict[str, Any]]:
        if sort_by in ("foreigner_net_buy", "institution_net_buy"):
            if self.country != "KR":
                raise ValueError("투자자별 순매수 랭킹은 국내 주식만 있어요")
            return await self._screen_investor(sort_by.split("_")[0], market, limit)
        type_, duration = {
            "trading_value": ("MARKET_TRADING_AMOUNT", "1d"),
            "volume": ("MARKET_TRADING_VOLUME", "1d"),
            "change_1d": ("TOP_GAINERS", "1d"),
            "losers_1d": ("TOP_LOSERS", "1d"),
            "change_1w": ("TOP_GAINERS", "1w"),
            "change_1mo": ("TOP_GAINERS", "1mo"),
            "market_cap": ("MARKET_TRADING_AMOUNT", "1d"),
        }.get(sort_by, ("MARKET_TRADING_AMOUNT", "1d"))
        if self.country == "KR":
            rows = await self.client.rankings(type_, duration, 100)
        else:
            rows = await self.client.rankings(type_, duration, 100, market_country=self.country)
        uni = await self.universe()
        rows = [r for r in rows if str(r["symbol"]) in uni]  # 유니버스(국내 보통주 / 미국 주식·ETF)만
        info = await self.info([str(r["symbol"]) for r in rows])
        out = []
        for r in rows:
            sym = str(r["symbol"])
            mk = uni.get(sym, {}).get("market", "")
            if market != "ALL" and mk != market:
                continue
            px = r.get("price") or {}
            last = _num(px.get("lastPrice"))
            mcap = last * _num(info.get(sym, {}).get("sharesOutstanding"))
            if mcap < min_market_cap:
                continue
            rate = px.get("changeRate")
            if self.country != "KR":  # 달러: 거래대금 백만 달러, 시가총액 십억 달러
                out.append(
                    {
                        "symbol": sym,
                        "name": uni.get(sym, {}).get("name", ""),
                        "english_name": info.get(sym, {}).get("englishName"),
                        "market": mk,
                        "type": uni.get(sym, {}).get("securityType", "STOCK"),
                        "price": last,
                        "change_pct": round(_num(rate) * 100, 2) if rate is not None else None,
                        "trading_value_musd": round(_num(r.get("tradingAmount")) / 1e6, 1),
                        "market_cap_busd": round(mcap / 1e9, 2) if mcap else None,
                    }
                )
                continue
            out.append(
                {
                    "symbol": sym,
                    "name": uni.get(sym, {}).get("name", ""),
                    "market": mk,
                    "price": last,
                    "change_pct": round(_num(rate) * 100, 2) if rate is not None else None,
                    "trading_value_eok": round(_num(r.get("tradingAmount")) / 1e8, 1),
                    "market_cap_eok": round(mcap / 1e8, 1) if mcap else None,
                }
            )
        if sort_by == "market_cap":
            key = "market_cap_eok" if self.country == "KR" else "market_cap_busd"
            out.sort(key=lambda x: -(x[key] or 0))
        return out[: max(1, min(30, limit))]

    async def _screen_investor(self, who: str, market: str, limit: int) -> list[dict[str, Any]]:
        grp = (await self.client.investor_rankings(100)).get(who) or {}
        uni = await self.universe()
        rows = [r for r in grp.get("netBuyRankings", []) if str(r["symbol"]) in uni]
        prices = await self.client.prices([str(r["symbol"]) for r in rows[:50]])
        out = []
        for r in rows:
            sym = str(r["symbol"])
            mk = uni[sym].get("market", "")
            if market != "ALL" and mk != market:
                continue
            out.append(
                {
                    "symbol": sym,
                    "name": uni[sym].get("name", ""),
                    "market": mk,
                    "price": prices.get(sym, (None, None))[0],
                    f"{who}_net_buy_eok": round(_num(r.get("netBuyAmount")) / 1e8, 1),
                }
            )
        return out[: max(1, min(30, limit))]

    async def find(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """종목 고르기용 검색 (메모 카드, D-52): 코드·티커 앞자리 또는 이름(한글·영문) 일부, 대소문자 무시.

        정렬: 코드 일치 → 코드 앞자리 → 이름 앞부분 → 이름 일부 (같으면 짧은 코드·코드순).
        """
        q = q.strip()
        if not q:
            return []
        uni = await self.universe()
        qu, ql = q.upper(), q.casefold()
        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        for sym, v in uni.items():
            su = sym.upper()
            names = [str(v.get("name") or "").casefold()]
            eng = v.get("englishName") or (self._info.get(sym) or {}).get("englishName")
            if eng:
                names.append(str(eng).casefold())
            if su == qu:
                rank = 0
            elif su.startswith(qu):
                rank = 1
            elif any(n.startswith(ql) for n in names):
                rank = 2
            elif any(ql in n for n in names):
                rank = 3
            else:
                continue
            ranked.append((rank, len(sym), sym, v))
        ranked.sort(key=lambda x: x[:3])
        return [
            {"symbol": str(v["symbol"]), "name": str(v.get("name", "")), "market": str(v.get("market", ""))}
            for _rank, _len, _sym, v in ranked[: max(1, limit)]
        ]

    async def search(self, q: str, limit: int = 50) -> list[dict[str, Any]]:
        uni = await self.universe()
        qu = q.upper()
        hits = [v for k, v in uni.items() if not q or q in k or qu in k or q in str(v.get("name", ""))]
        return [
            {"symbol": v["symbol"], "name": v.get("name", ""), "market": v.get("market", "")}
            for v in hits[:limit]
        ]
