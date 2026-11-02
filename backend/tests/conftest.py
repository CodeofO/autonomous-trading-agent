"""공통 픽스처.

- 모든 테스트에서 외부 네트워크 접속을 차단한다(localhost 제외). respx/ASGI 전송은 소켓을 쓰지 않으므로 영향 없음.
- 실제 .env 를 읽지 않도록 환경을 격리한다.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


class NetworkBlockedError(RuntimeError):
    pass


def _host_of(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # unix socket 등


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    host = _host_of(address)
    if host is not None and host not in _ALLOWED_HOSTS:
        raise NetworkBlockedError(f"테스트 중 외부 네트워크 접속 차단: {host}")
    return _real_connect(self, address)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
    host = _host_of(address)
    if host is not None and host not in _ALLOWED_HOSTS:
        raise NetworkBlockedError(f"테스트 중 외부 네트워크 접속 차단: {host}")
    return _real_create_connection(address, *args, **kwargs)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _guarded_create_connection)
    yield


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "DATA_GO_KR_SERVICE_KEY",
        "DART_API_KEY",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "DATA_SOURCE",
        "CLOCK_MODE",
        "DATABASE_URL",
        "TRADING_MODE",
        "APP_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    from app.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
