"""애플리케이션 설정.

비밀정보(데이터 API 키, LLM 키, APP_SECRET_KEY)는 .env 로만 주입하며 SecretStr 로 보관한다.
TRADING_MODE 는 "paper" 외의 값을 허용하지 않는다 — 실계좌(live) 주문 코드 경로는 존재하지 않는다(D-01).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _filled(s: SecretStr | None) -> bool:
    return bool(s is not None and s.get_secret_value().strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 안전 장치 -------------------------------------------------------
    trading_mode: Literal["paper"] = "paper"

    # --- 데이터 소스 -----------------------------------------------------
    data_source: Literal["auto", "mock", "public"] = "auto"
    data_go_kr_service_key: SecretStr | None = None
    dart_api_key: SecretStr | None = None
    naver_client_id: SecretStr | None = None
    naver_client_secret: SecretStr | None = None

    # --- LLM (OpenAI 호환 Chat Completions) -------------------------------
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_tool_mode: Literal["auto", "tools", "json"] = "auto"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096
    llm_max_steps: int = Field(default=24, ge=2, le=80)
    llm_timeout_sec: float = 180.0

    # --- 시계 ------------------------------------------------------------
    clock_mode: Literal["auto", "real", "simulated"] = "auto"
    sim_speed: float = Field(default=600.0, gt=0, description="시뮬레이션 배속 (600 = 실제 1초에 10분)")
    sim_start: str = ""
    mock_seed: int = 42

    # --- 모의 계좌 -------------------------------------------------------
    paper_initial_cash: float = 10_000_000
    paper_commission_rate: float = 0.00015
    paper_sell_tax_rate: float = 0.0020
    paper_slippage_bps: float = 5.0
    paper_max_volume_participation: float = Field(default=0.05, gt=0, le=1)

    # --- 앱 --------------------------------------------------------------
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"
    data_dir: Path = BACKEND_DIR / "data"
    app_secret_key: SecretStr | None = None
    session_ttl_hours: int = 12
    log_level: str = "INFO"
    enable_scheduler: bool = True

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _only_paper(cls, v: object) -> object:
        if v is None or v == "":
            return "paper"
        if str(v).strip().lower() != "paper":
            raise ValueError(
                "TRADING_MODE 는 'paper' 만 허용됩니다. 이 시스템에는 실계좌(live) 주문 경로가 없습니다."
            )
        return "paper"

    @field_validator("llm_base_url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        return v.strip().rstrip("/")

    # --- 파생 값 ---------------------------------------------------------
    @property
    def has_public_data(self) -> bool:
        return _filled(self.data_go_kr_service_key)

    @property
    def has_dart(self) -> bool:
        return _filled(self.dart_api_key)

    @property
    def has_naver(self) -> bool:
        return _filled(self.naver_client_id) and _filled(self.naver_client_secret)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and _filled(self.llm_api_key))

    @property
    def resolved_data_source(self) -> Literal["mock", "public"]:
        if self.data_source == "auto":
            return "public" if self.has_public_data else "mock"
        if self.data_source == "public" and not self.has_public_data:
            raise ValueError("DATA_SOURCE=public 에는 DATA_GO_KR_SERVICE_KEY 가 필요합니다.")
        return self.data_source

    @property
    def resolved_clock_mode(self) -> Literal["real", "simulated"]:
        if self.clock_mode == "auto":
            return "simulated" if self.resolved_data_source == "mock" else "real"
        if self.clock_mode == "simulated" and self.resolved_data_source != "mock":
            raise ValueError("CLOCK_MODE=simulated 는 Mock 데이터에서만 쓸 수 있습니다(실데이터와 시각이 어긋남).")
        return self.clock_mode

    def secret_values(self) -> list[str]:
        """로그 마스킹 대상 비밀값 목록."""
        out: list[str] = []
        for s in (
            self.data_go_kr_service_key,
            self.dart_api_key,
            self.naver_client_id,
            self.naver_client_secret,
            self.llm_api_key,
            self.app_secret_key,
        ):
            if _filled(s):
                assert s is not None
                out.append(s.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
