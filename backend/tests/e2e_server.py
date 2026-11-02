"""Playwright E2E 용 백엔드 (테스트 전용): 가짜 토스 + 고정 시각(2026-11-02 08:40 KST), 실제 외부 호출 없음."""

from __future__ import annotations

import os
from datetime import datetime

import uvicorn

from app.core.clock import KST, SimulatedClock
from app.core.config import Settings
from app.main import create_app
from tests.fake_toss import FakeTossClient


def main() -> None:
    clock = SimulatedClock(datetime(2026, 11, 2, 8, 40, tzinfo=KST))
    settings = Settings(enable_scheduler=False, llm_base_url="", llm_model="", llm_api_key=None)
    app = create_app(settings, toss_client=FakeTossClient(clock), clock=clock)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_PORT", "8001")), log_level="warning")


if __name__ == "__main__":
    main()
