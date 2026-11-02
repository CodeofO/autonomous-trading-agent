from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.core.clock import KST, SimulatedClock
from app.core.http import SafeHttpClient
from app.core.market_calendar import MarketCalendar
from app.db.session import Database
from app.market import public_data
from app.market.mock_world import COMPANIES, MockDataProvider, MockWorld
from app.market.public_data import PublicDataError, PublicDataProvider
from app.market.store import PriceStore
from app.research import dart, naver
from app.research.dart import DartClient, extract_summary
from app.research.naver import NaverNewsClient

FIX = Path(__file__).parent / "fixtures"


def _fx(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _http(routes) -> SafeHttpClient:
    return SafeHttpClient(routes, rate_per_sec=1000, backoff_scale=0.0)


# ---------------------------------------------------------------- 공공데이터
@respx.mock
async def test_public_fetch_day_parses_and_filters() -> None:
    route = respx.get(public_data.URL).mock(
        return_value=httpx.Response(200, json=_fx("public_data_day.json"))
    )
    p = PublicDataProvider("abc%2Bdef%3D%3D", http=_http(public_data.ROUTES))
    bars = await p.fetch_day(date(2026, 10, 30))
    assert [b.symbol for b in bars] == ["005930", "091990"]  # KONEX 제외
    b = bars[0]
    assert (b.open, b.high, b.low, b.close) == (71500, 72300, 71200, 72000)
    assert b.market_cap == 429824343600000 and b.name == "삼성전자"
    q = dict(route.calls[0].request.url.params)
    assert q["basDt"] == "20261030" and q["resultType"] == "json"
    assert q["serviceKey"] == "abc+def=="  # Encoding 키를 넣어도 디코딩 후 1회만 인코딩


@respx.mock
async def test_public_single_item_object_and_history_filter() -> None:
    payload = _fx("public_data_day.json")
    items = payload["response"]["body"]["items"]["item"]
    payload["response"]["body"]["items"]["item"] = items[0]  # 1건 객체 형태
    payload["response"]["body"]["totalCount"] = 1
    route = respx.get(public_data.URL).mock(return_value=httpx.Response(200, json=payload))
    p = PublicDataProvider("k", http=_http(public_data.ROUTES))
    bars = await p.fetch_history("005930", date(2026, 10, 1), date(2026, 10, 30))
    assert len(bars) == 1
    q = dict(route.calls[0].request.url.params)
    assert q["likeSrtnCd"] == "005930" and q["endBasDt"] == "20261031"


@respx.mock
async def test_public_paging() -> None:
    first = _fx("public_data_day.json")
    first["response"]["body"]["totalCount"] = 1500
    second = _fx("public_data_day.json")
    respx.get(public_data.URL).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    p = PublicDataProvider("k", http=_http(public_data.ROUTES))
    assert len(await p.fetch_day(date(2026, 10, 30))) == 4


@respx.mock
async def test_public_error_code_and_non_json() -> None:
    respx.get(public_data.URL).mock(
        return_value=httpx.Response(
            200, json={"response": {"header": {"resultCode": "30", "resultMsg": "KEY ERR"}}}
        )
    )
    p = PublicDataProvider("k", http=_http(public_data.ROUTES))
    with pytest.raises(PublicDataError, match="30"):
        await p.fetch_day(date(2026, 10, 30))
    respx.get(public_data.URL).mock(return_value=httpx.Response(200, text="<OpenAPI_ServiceResponse>"))
    with pytest.raises(PublicDataError, match="JSON"):
        await p.fetch_day(date(2026, 10, 30))


@respx.mock
async def test_retry_on_429_then_success() -> None:
    route = respx.get(public_data.URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(200, json=_fx("public_data_day.json")),
        ]
    )
    p = PublicDataProvider("k", http=_http(public_data.ROUTES))
    assert len(await p.fetch_day(date(2026, 10, 30))) == 2
    assert route.call_count == 3


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


# ---------------------------------------------------------------- Mock / Store
async def test_mock_provider_respects_publish_time() -> None:
    cal = MarketCalendar()
    clock = SimulatedClock(datetime(2026, 11, 2, 12, 59, tzinfo=KST))
    p = MockDataProvider(MockWorld(7, cal), clock)
    assert await p.fetch_day(date(2026, 10, 30)) == []  # 공개 전
    clock.set(datetime(2026, 11, 2, 13, 0, tzinfo=KST))
    assert len(await p.fetch_day(date(2026, 10, 30))) == len(COMPANIES)
    hist = await p.fetch_history("MOCK01", date(2026, 10, 1), date(2026, 11, 30))
    assert hist[-1].day == date(2026, 10, 30)


def test_mock_news_no_lookahead_and_deterministic() -> None:
    cal = MarketCalendar()
    w1, w2 = MockWorld(7, cal), MockWorld(7, cal)
    now = datetime(2026, 11, 2, 8, 0, tzinfo=KST)
    n1 = w1.news("", now, 50, lookback_days=60)
    assert n1 == w2.news("", now, 50, lookback_days=60)
    assert all(datetime.fromisoformat(n["pubDate"]) <= now for n in n1)
    fin = w1.financials("MOCK03", now)
    assert fin["available"] and len(fin["reports"]) == 4
    assert fin["reports"][0]["period"] == "2026Q2"  # 3분기는 11/14 이후 공개


async def test_store_sync_and_history() -> None:
    db = Database("sqlite://")
    db.create_all()
    cal = MarketCalendar()
    clock = SimulatedClock(datetime(2026, 11, 2, 14, 0, tzinfo=KST))
    store = PriceStore(db, MockDataProvider(MockWorld(7, cal), clock))
    assert await store.sync_day(date(2026, 10, 30)) == len(COMPANIES)
    assert await store.sync_day(date(2026, 10, 30)) == 0
    await store.ensure_history("MOCK02", date(2026, 10, 30), 90)
    h = store.history("MOCK02", n=500)
    assert len(h) > 55 and h[-1].day == date(2026, 10, 30)
    assert [b.day for b in h] == sorted(b.day for b in h)
    assert store.latest_day() == date(2026, 10, 30)
    assert len(store.snapshot()) == len(COMPANIES)
    assert store.name_of("MOCK02") == "한빛반도체"
