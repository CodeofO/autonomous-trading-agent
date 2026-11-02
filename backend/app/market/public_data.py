"""금융위원회_주식시세정보 (공공데이터포털) 클라이언트 — 조회 전용.

docs/DATA_API_NOTES.md §1 참고. T+1 일봉만 제공한다.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote

from app.core.http import Route, SafeHttpClient
from app.market.types import Bar

log = logging.getLogger(__name__)

HOST = "apis.data.go.kr"
URL = f"https://{HOST}/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
ROUTES = [Route(HOST, "GET", re.compile(r"/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"))]
MARKETS = {"KOSPI", "KOSDAQ"}
PAGE_SIZE = 1000


class PublicDataError(RuntimeError):
    pass


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def _items(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    resp = payload.get("response", payload)
    header = resp.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "0", "000"):
        raise PublicDataError(f"공공데이터 오류 {code}: {header.get('resultMsg', '')}")
    body = resp.get("body") or {}
    items = body.get("items") or {}
    item = items.get("item") if isinstance(items, dict) else items
    if item is None:
        rows: list[dict[str, Any]] = []
    elif isinstance(item, dict):  # TODO(verify): 1건일 때 객체로 오는 경우 대비
        rows = [item]
    else:
        rows = list(item)
    return rows, int(_num(body.get("totalCount", len(rows))))


def parse_item(it: dict[str, Any]) -> Bar:
    return Bar(
        symbol=str(it.get("srtnCd", "")).strip(),
        day=date(int(it["basDt"][:4]), int(it["basDt"][4:6]), int(it["basDt"][6:8])),
        open=_num(it.get("mkp")),
        high=_num(it.get("hipr")),
        low=_num(it.get("lopr")),
        close=_num(it.get("clpr")),
        volume=_num(it.get("trqu")),
        value=_num(it.get("trPrc")),
        market_cap=_num(it.get("mrktTotAmt")),
        listed_shares=_num(it.get("lstgStCnt")),
        name=str(it.get("itmsNm", "")).strip(),
        market=str(it.get("mrktCtg", "")).strip(),
    )


def _valid(b: Bar) -> bool:
    # 거래정지 종목은 시가·고가·저가가 0 으로 올 수 있다 → 종가로 채우거나 제외
    return b.close > 0


class PublicDataProvider:
    name = "public"

    def __init__(self, service_key: str, http: SafeHttpClient | None = None) -> None:
        # 포털의 "Encoding" 키를 넣어도 이중 인코딩되지 않도록 디코딩해서 보관
        self._key = unquote(service_key.strip())
        self.http = http or SafeHttpClient(ROUTES, rate_per_sec=5)

    async def _get(self, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        q = {"serviceKey": self._key, "resultType": "json", **params}
        r = await self.http.request("GET", URL, params=q)
        if r.status_code != 200:
            raise PublicDataError(f"공공데이터 HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:  # 키 오류 시 XML 오류문이 오는 경우가 있다
            raise PublicDataError("공공데이터 응답이 JSON 이 아닙니다(서비스키 확인 필요)") from e
        return _items(payload)

    async def _paged(self, params: dict[str, Any]) -> list[Bar]:
        out: list[Bar] = []
        page = 1
        while True:
            rows, total = await self._get({**params, "numOfRows": PAGE_SIZE, "pageNo": page})
            for it in rows:
                try:
                    b = parse_item(it)
                except (KeyError, ValueError):
                    continue
                if b.market in MARKETS and _valid(b):
                    out.append(_fill_ohl(b))
            if page * PAGE_SIZE >= total or not rows:
                return out
            page += 1

    async def fetch_day(self, day: date) -> list[Bar]:
        return await self._paged({"basDt": day.strftime("%Y%m%d")})

    async def fetch_history(self, symbol: str, start: date, end: date) -> list[Bar]:
        bars = await self._paged(
            {
                "likeSrtnCd": symbol,
                "beginBasDt": start.strftime("%Y%m%d"),
                # endBasDt 는 '미만' 조건이라 하루 더한다 (TODO(verify))
                "endBasDt": (end + timedelta(days=1)).strftime("%Y%m%d"),
            }
        )
        return sorted((b for b in bars if b.symbol == symbol and start <= b.day <= end), key=lambda b: b.day)


def _fill_ohl(b: Bar) -> Bar:
    if b.open > 0 and b.high > 0 and b.low > 0:
        return b
    o = b.open or b.close
    return Bar(**{**b.__dict__, "open": o, "high": b.high or max(o, b.close), "low": b.low or min(o, b.close)})
