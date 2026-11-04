"""네이버 뉴스 키 인증 실패(401): 이유를 알려 주고, 재시도하지 않고, 화면에 표시하고, 점검 명령으로 확인 (D-44, 실제 호출 없음)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.http import SafeHttpClient
from app.research import check, naver
from app.research.naver import NaverError, NaverNewsClient
from tests.test_api import _client

KEYS = {"naver_client_id": "id-test-0001", "naver_client_secret": "secret-test"}
AUTH_FAIL = httpx.Response(
    401, json={"errorMessage": "Authentication failed. (인증에 실패했습니다.)", "errorCode": "024"}
)
OK = httpx.Response(
    200,
    json={
        "items": [{"title": "<b>코스피</b> 반등", "description": "", "link": "https://n.news.naver.com/a/1"}]
    },
)


def _naver() -> NaverNewsClient:
    return NaverNewsClient(
        "id", "secret", http=SafeHttpClient(naver.ROUTES, rate_per_sec=1000, backoff_scale=0.0)
    )


@respx.mock
async def test_auth_failure_explains_fix_and_stops_calling() -> None:
    route = respx.get(naver.HUB_URL).mock(return_value=AUTH_FAIL)
    c = _naver()
    with pytest.raises(
        NaverError, match=r"키 인증 실패\(HTTP 401 024\).*NAVER_CLIENT_ID / NAVER_CLIENT_SECRET"
    ):
        await c.search("삼성전자")
    assert c.auth_error and "secret" not in c.auth_error  # 키 값은 메시지에 없다
    with pytest.raises(NaverError, match="키 인증 실패"):
        await c.search("현대차")
    assert route.call_count == 1  # 같은 키로 다시 부르지 않는다(재시작 전까지)


@respx.mock
async def test_other_errors_do_not_stop_search() -> None:
    route = respx.get(naver.HUB_URL).mock(side_effect=[httpx.Response(400, json={"errorCode": "SE01"}), OK])
    c = _naver()
    with pytest.raises(NaverError, match="HTTP 400 SE01"):
        await c.search("x")
    assert c.auth_error is None
    assert (await c.search("코스피"))[0]["title"] == "코스피 반등"
    assert route.call_count == 2


def test_structure_shows_auth_failure(tmp_path: Path) -> None:
    with _client(tmp_path, auth_enabled=False, **KEYS) as client:
        src = {s["name"]: s for s in client.get("/api/agent/structure").json()["sources"]}
        assert src["NAVER"]["ready"] is True and src["NAVER"]["note"] is None
        client.app.state.container.research.news_client.auth_error = "네이버 키 인증 실패(HTTP 401 024)"  # type: ignore[attr-defined]
        st = client.get("/api/agent/structure").json()
        src = {s["name"]: s for s in st["sources"]}
        assert src["NAVER"]["ready"] is False and src["NAVER"]["note"] == "키 인증 실패"
        news = next(t for t in st["tools"] if t["name"] == "search_news")
        assert news["ready"] is False


def _settings(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    monkeypatch.setattr(check, "get_settings", lambda: Settings(**kw))


def test_check_needs_keys(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _settings(monkeypatch)
    assert check.main([]) == 2 and "NAVER_CLIENT_ID" in capsys.readouterr().out


@respx.mock
def test_check_reports_ok_and_auth_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _settings(monkeypatch, **KEYS)
    route = respx.get(naver.HUB_URL).mock(side_effect=[OK, AUTH_FAIL])
    assert check.main(["코스피"]) == 0
    out = capsys.readouterr().out
    assert "✅" in out and "코스피 반등" in out and "secret-test" not in out
    assert check.main([]) == 1
    out = capsys.readouterr().out
    assert "❌" in out and "024" in out and "secret-test" not in out
    assert route.calls[0].request.url.params["query"] == "코스피"


@respx.mock
async def test_hub_sends_ncp_headers_and_json_format() -> None:
    """NAVER API HUB: 공식 문서의 주소·헤더(X-NCP-APIGW-API-KEY-ID / -KEY)·format=json."""
    route = respx.get(naver.HUB_URL).mock(return_value=OK)
    c = NaverNewsClient(" hub-id ", "hub-secret", http=_naver().http)
    assert (await c.search("코스피", display=3))[0]["link"] == "https://n.news.naver.com/a/1"
    req = route.calls[0].request
    assert req.headers["X-NCP-APIGW-API-KEY-ID"] == "hub-id"
    assert req.headers["X-NCP-APIGW-API-KEY"] == "hub-secret"
    assert "X-Naver-Client-Id" not in req.headers
    p = req.url.params
    assert (p["query"], p["display"], p["sort"], p["format"]) == ("코스피", "3", "date", "json")


@respx.mock
async def test_gateway_error_shape_and_daily_limit() -> None:
    """API 게이트웨이 오류는 {error: {errorCode, message}} 로 온다. 429 는 한도 초과로 알리고 멈추지 않는다."""
    route = respx.get(naver.HUB_URL).mock(
        return_value=httpx.Response(429, json={"error": {"errorCode": "429", "message": "Quota Exceeded"}})
    )
    c = _naver()
    with pytest.raises(NaverError, match=r"하루 호출 한도 초과\(HTTP 429 429\)"):
        await c.search("x")  # SafeHttpClient 가 429 를 몇 번 다시 시도한 뒤 포기
    assert c.auth_error is None
    route.mock(
        return_value=httpx.Response(
            401, json={"error": {"errorCode": "200", "message": "Authentication Failed"}}
        )
    )
    with pytest.raises(NaverError, match=r"HTTP 401 200\) — NAVER API HUB"):
        await c.search("x")


@respx.mock
async def test_developers_endpoint_hint_points_to_hub_option() -> None:
    route = respx.get(naver.URL).mock(return_value=AUTH_FAIL)
    c = NaverNewsClient("id", "secret", http=_naver().http, api="developers")
    with pytest.raises(NaverError, match="NAVER_API=hub"):
        await c.search("x")
    assert "format" not in route.calls[0].request.url.params
