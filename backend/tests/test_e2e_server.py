"""e2e 서버는 실제 .env 를 읽지 않는다 (외부 호출 차단)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from tests.e2e_server import isolated_settings


def test_e2e_server_ignores_dotenv_and_external_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "LLM_TEST_BASE_URL=http://real-llm.example/v1\nLLM_TEST_MODEL=real\nNAVER_CLIENT_ID=real\n"
    )
    monkeypatch.setitem(Settings.model_config, "env_file", str(env))
    monkeypatch.setenv("LLM_TEST_API_KEY", "sk-real-should-not-be-used")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = isolated_settings()
    assert not s.has_test_llm and not s.has_llm and not s.has_naver and not s.has_google_search
    assert s.llm_test_base_url == "" and str(s.data_dir) == str(tmp_path)  # 실행 명령의 환경변수는 그대로
