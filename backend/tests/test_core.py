from __future__ import annotations

import logging
import socket
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.clock import KST
from app.core.config import Settings
from app.core.logging import RedactingFilter, mask, redact, register_secret
from app.core.market_calendar import MarketCalendar
from app.core.ticks import round_to_tick, tick_size
from app.main import create_app


def test_health() -> None:
    client = TestClient(create_app(Settings(database_url="sqlite://", enable_scheduler=False)))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["trading_mode"] == "paper"


@pytest.mark.parametrize("value", ["live", "LIVE", "real", "prod"])
def test_trading_mode_only_paper(value: str) -> None:
    with pytest.raises(ValueError, match="paper"):
        Settings(trading_mode=value)  # type: ignore[arg-type]


def test_trading_mode_env_live_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(ValueError):
        Settings()


def test_data_source_auto_resolution() -> None:
    assert Settings().resolved_data_source == "mock"
    assert Settings().resolved_clock_mode == "simulated"
    assert not Settings().has_llm
    s = Settings(data_go_kr_service_key="abcd1234")  # type: ignore[arg-type]
    assert s.resolved_data_source == "public"
    assert s.resolved_clock_mode == "real"
    llm = Settings(llm_base_url="https://llm.example/v1/", llm_model="m", llm_api_key="k")  # type: ignore[arg-type]
    assert llm.has_llm and llm.llm_base_url == "https://llm.example/v1"


def test_simulated_clock_requires_mock() -> None:
    s = Settings(data_go_kr_service_key="abcd1234", clock_mode="simulated")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _ = s.resolved_clock_mode


def test_secret_not_in_repr() -> None:
    s = Settings(llm_api_key="supersecretvalue", dart_api_key="dartsecret123")  # type: ignore[arg-type]
    assert "supersecretvalue" not in repr(s)
    assert "supersecretvalue" not in s.model_dump_json()
    assert "dartsecret123" not in repr(s)
    assert set(s.secret_values()) == {"supersecretvalue", "dartsecret123"}


def test_mask_and_redact() -> None:
    assert mask("abcdefgh") == "abcd****"
    register_secret("my-very-secret-key-123")
    out = redact("key=my-very-secret-key-123 and Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig client_secret=s_xyz12345")
    assert "my-very-secret-key-123" not in out
    assert "eyJhbGciOiJSUzI1NiJ9.payload.sig" not in out
    assert "s_xyz12345" not in out
    assert "my-v****" in out


def test_logging_filter_masks(caplog: pytest.LogCaptureFixture) -> None:
    register_secret("tsck_live_abcdefg")
    logger = logging.getLogger("test.mask")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("token for %s", "tsck_live_abcdefg")
    assert "tsck_live_abcdefg" not in caplog.text
    assert "tsck****" in caplog.text


def test_network_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="차단"):
        socket.create_connection(("8.8.8.8", 443), timeout=1)


def test_calendar_basic() -> None:
    cal = MarketCalendar()
    assert cal.is_trading_day(date(2026, 11, 2))  # 월
    assert not cal.is_trading_day(date(2026, 11, 1))  # 일
    assert not cal.is_trading_day(date(2026, 12, 25))  # 성탄절
    t = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
    assert cal.is_order_allowed(t)
    assert not cal.is_order_allowed(datetime(2026, 11, 2, 8, 59, 59, tzinfo=KST))
    assert not cal.is_order_allowed(datetime(2026, 11, 2, 15, 20, tzinfo=KST))
    assert cal.is_order_allowed(datetime(2026, 11, 2, 15, 19, 59, tzinfo=KST))
    assert cal.is_market_open(datetime(2026, 11, 2, 15, 25, tzinfo=KST))
    assert cal.next_session(datetime(2026, 10, 31, 12, 0, tzinfo=KST)).day == date(2026, 11, 2)


def test_calendar_publish_and_fill_basis() -> None:
    cal = MarketCalendar()
    # 2026-10-30(금) 데이터는 2026-11-02(월) 13:00 공개
    assert cal.publish_time(date(2026, 10, 30)) == datetime(2026, 11, 2, 13, 0, tzinfo=KST)
    assert cal.latest_published_day(datetime(2026, 11, 2, 12, 59, tzinfo=KST)) == date(2026, 10, 29)
    assert cal.latest_published_day(datetime(2026, 11, 2, 13, 0, tzinfo=KST)) == date(2026, 10, 30)
    assert cal.fill_basis(datetime(2026, 11, 2, 8, 29, tzinfo=KST)) is None
    assert cal.fill_basis(datetime(2026, 11, 2, 8, 30, tzinfo=KST)) == "OPEN"
    assert cal.fill_basis(datetime(2026, 11, 2, 9, 0, tzinfo=KST)) == "CLOSE"
    assert cal.fill_basis(datetime(2026, 11, 2, 15, 19, tzinfo=KST)) == "CLOSE"
    assert cal.fill_basis(datetime(2026, 11, 2, 15, 20, tzinfo=KST)) is None
    assert cal.fill_basis(datetime(2026, 11, 1, 10, 0, tzinfo=KST)) is None  # 일요일
    assert cal.next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)


@pytest.mark.parametrize(
    ("price", "tick"),
    [(1999, 1), (2000, 5), (4999, 5), (5000, 10), (19990, 10), (20000, 50), (49950, 50), (50000, 100),
     (199900, 100), (200000, 500), (500000, 1000)],
)
def test_tick_size(price: float, tick: int) -> None:
    assert tick_size(price) == tick


def test_round_to_tick() -> None:
    assert round_to_tick(72_049) == 72_000
    assert round_to_tick(72_051, "up") == 72_100
    assert round_to_tick(72_099, "down") == 72_000
