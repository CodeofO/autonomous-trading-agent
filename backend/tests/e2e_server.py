"""Playwright E2E 용 백엔드 (테스트 전용): 가짜 토스 + 고정 시각(2026-11-02 08:40 KST), 실제 외부 호출 없음."""

from __future__ import annotations

import os
from datetime import datetime

import uvicorn

from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.main import create_app
from tests.fake_toss import FakeTossClient

# 실제 .env 를 읽지 않는다: 읽으면 사용자의 LLM_TEST_*·네이버·DART·Gemini 키로 실제 외부 호출이 나간다
# (2026-11-04: e2e 판단 세션이 실제 테스트용 LLM 서버로 요청을 보낸 일이 있었음). DATABASE_URL·DATA_DIR 처럼
# 실행 명령이 넘기는 환경변수는 그대로 쓰고, 외부 키 환경변수는 지운다.
EXTERNAL_KEYS = (
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_TEST_BASE_URL",
    "LLM_TEST_MODEL",
    "LLM_TEST_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "DART_API_KEY",
    "GEMINI_API_KEY",
    "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET",
    "VAPID_PRIVATE_KEY",
    "TRADING_MODE",
    "LIVE_TRADING_CONFIRM",
)


def isolated_settings() -> Settings:
    for key in EXTERNAL_KEYS:
        os.environ.pop(key, None)
    Settings.model_config["env_file"] = None
    return Settings(enable_scheduler=False)


def main() -> None:
    clock = SimulatedClock(datetime(2026, 11, 2, 8, 40, tzinfo=KST))
    settings = isolated_settings()
    app = create_app(settings, toss_client=FakeTossClient(clock), clock=clock)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_PORT", "8001")), log_level="warning")


if __name__ == "__main__":
    main()
