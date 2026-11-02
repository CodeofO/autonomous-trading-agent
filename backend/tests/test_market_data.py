from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from app.core.http import SafeHttpClient
from app.core.market_calendar import MarketCalendar
from app.db.session import Database
from app.market.store import PriceStore
from app.research import dart, naver
from app.research.dart import DartClient, extract_summary
from app.research.naver import NaverNewsClient

FIX = Path(__file__).parent / "fixtures"


def _fx(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _http(routes) -> SafeHttpClient:
    return SafeHttpClient(routes, rate_per_sec=1000, backoff_scale=0.0)


# ---------------------------------------------------------------- DART
def _corp_zip() -> bytes:
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?><result>"
        "<list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code>"
        "<modify_date>20260101</modify_date></list>"
        "<list><corp_code>00999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>"
        "</result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml.encode())
    return buf.getvalue()


def test_extract_summary_by_id_and_name() -> None:
    s = extract_summary(_fx("dart_fnltt.json")["list"])
    assert s["total_assets"] == 500_000_000
    assert s["revenue"] == 80_000_000
    assert s["net_income"] == 7_000_000  # 계정명(반기순이익)으로 매칭
    assert s["depreciation"] == 6_000_000
    assert s["capex"] == -12_000_000
    prior = extract_summary(_fx("dart_fnltt.json")["list"], "frmtrm_q_amount")
    assert prior["operating_income"] == 7_000_000


@respx.mock
async def test_dart_financials_and_disclosures() -> None:
    db = Database("sqlite://")
    db.create_all()
    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(
        return_value=httpx.Response(200, content=_corp_zip())
    )
    fs_route = respx.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json")

    def fs_handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if p["bsns_year"] == "2026" and p["reprt_code"] == "11012" and p["fs_div"] == "CFS":
            return httpx.Response(200, json=_fx("dart_fnltt.json"))
        if p["bsns_year"] == "2025" and p["reprt_code"] == "11011" and p["fs_div"] == "CFS":
            return httpx.Response(200, json=_fx("dart_fnltt.json"))
        return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

    fs_route.mock(side_effect=fs_handler)
    respx.get("https://opendart.fss.or.kr/api/list.json").mock(
        return_value=httpx.Response(200, json=_fx("dart_list.json"))
    )
    c = DartClient("k" * 40, db, http=_http(dart.ROUTES))
    fin = await c.financials("005930", date(2026, 11, 2))
    assert fin["available"] and [r["period"] for r in fin["reports"]] == ["2026 반기", "2025 사업보고서"]
    assert fin["reports"][0]["revenue"] == 80_000_000
    calls = fs_route.call_count
    await c.financials("005930", date(2026, 11, 2))
    assert fs_route.call_count == calls  # 캐시 사용
    disc = await c.disclosures("005930", date(2026, 11, 2))
    assert disc[0]["report_nm"] == "단일판매ㆍ공급계약체결"
    assert await c.financials("000000", date(2026, 11, 2)) == {
        "symbol": "000000",
        "available": False,
        "reason": "DART 고유번호 없음",
    }


@respx.mock
async def test_dart_error_status_raises() -> None:
    db = Database("sqlite://")
    db.create_all()
    respx.get("https://opendart.fss.or.kr/api/corpCode.xml").mock(
        return_value=httpx.Response(200, content=_corp_zip())
    )
    respx.get("https://opendart.fss.or.kr/api/list.json").mock(
        return_value=httpx.Response(200, json={"status": "010", "message": "등록되지 않은 키입니다."})
    )
    c = DartClient("k" * 40, db, http=_http(dart.ROUTES))
    with pytest.raises(dart.DartError, match="010"):
        await c.disclosures("005930", date(2026, 11, 2))


# ---------------------------------------------------------------- 네이버
@respx.mock
async def test_naver_news_cleans_tags_and_sends_headers() -> None:
    route = respx.get(naver.URL).mock(return_value=httpx.Response(200, json=_fx("naver_news.json")))
    c = NaverNewsClient("id123", "secret456", http=_http(naver.ROUTES))
    items = await c.search("삼성전자", display=500, sort="weird")
    assert items[0]["title"] == '삼성전자, 대규모 수주 "기대 이상"'
    assert items[0]["link"] == "https://news.example.com/a/1"
    assert items[1]["link"] == "https://n.news.naver.com/a/2"
    req = route.calls[0].request
    assert req.headers["X-Naver-Client-Id"] == "id123"
    assert req.url.params["display"] == "100" and req.url.params["sort"] == "date"


# ---------------------------------------------------------------- Store
async def test_store_upsert_history_and_ensure() -> None:
    from tests.helpers import StaticProvider, bar

    days = MarketCalendar().trading_days(date(2026, 6, 1), date(2026, 10, 30))
    provider = StaticProvider(
        [bar("005930", d, 70_000, 71_000, 69_000, 70_000 + i, name="삼성전자") for i, d in enumerate(days)]
    )
    db = Database("sqlite://")
    db.create_all()
    store = PriceStore(db, provider)
    await store.ensure_history("005930", date(2026, 10, 30), 120)
    h = store.history("005930", n=500)
    assert len(h) > 60 and h[-1].day == date(2026, 10, 30)
    assert [b.day for b in h] == sorted(b.day for b in h)
    assert store.latest_day() == date(2026, 10, 30) and store.name_of("005930") == "삼성전자"
    assert store.upsert([bar("005930", date(2026, 10, 30), 1, 1, 1, 99_999, name="삼성전자")]) == 1
    assert store.last_bar("005930").close == 99_999  # type: ignore[union-attr]
    assert await store.sync_day(date(2026, 10, 30)) == 0  # 이미 있음
