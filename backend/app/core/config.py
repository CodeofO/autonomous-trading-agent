"""애플리케이션 설정.

비밀정보(데이터 API 키, LLM 키, APP_SECRET_KEY)는 .env 로만 주입하며 SecretStr 로 보관한다.
TRADING_MODE 기본은 "paper"(자체 모의계좌). "live"(토스증권 실계좌 주문)는 사용자가 .env 에서
LIVE_TRADING_CONFIRM 확인 문구까지 넣어야만 켜진다(D-34). 켜는 것은 사용자의 판단이다.
시세는 토스증권 Open API(조회 전용)만 쓴다. 목업 데이터·가속 시뮬레이션은 없다(D-28).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
# 실거래를 켤 때 .env 의 LIVE_TRADING_CONFIRM 에 그대로 적어야 하는 문구 (실수로 켜지는 것 방지)
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_REAL_MONEY"


def _filled(s: SecretStr | None) -> bool:
    return bool(s is not None and s.get_secret_value().strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 거래 모드 -------------------------------------------------------
    # paper: 자체 모의계좌 (기본) | live: 토스증권 실계좌로 실제 주문
    trading_mode: Literal["paper", "live"] = "paper"
    live_trading_confirm: str = ""  # live 일 때 LIVE_CONFIRM_PHRASE 와 같아야 함
    toss_account_seq: str = ""  # 주문할 계좌. 비우면 계좌가 하나일 때 자동 선택
    # 실거래 하루 매수 총액 상한(원). 리스크 한도와 별개로 코드가 강제한다
    live_daily_buy_limit: float = Field(default=5_000_000, gt=0)

    # --- 데이터 (토스증권 조회 전용 + DART·네이버) -------------------------
    toss_client_id: SecretStr | None = None
    toss_client_secret: SecretStr | None = None
    dart_api_key: SecretStr | None = None
    naver_client_id: SecretStr | None = None
    naver_client_secret: SecretStr | None = None

    # --- LLM (OpenAI 호환 Chat Completions) -------------------------------
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_tool_mode: Literal["auto", "tools", "json"] = "auto"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_max_steps: int = Field(default=24, ge=2, le=80)
    llm_timeout_sec: float = 180.0

    # --- 판단 주기 --------------------------------------------------------
    # 장중 LLM 판단 간격(분). 60 → 10:00~15:00 매시 정각 (+ 08:35 장전). 0 → 14:00 한 번
    agent_interval_minutes: int = Field(default=60, ge=0, le=390)

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
    # 로컬 전용(127.0.0.1)이라 기본은 로그인 없음. 다른 사람이 접근할 수 있는 환경이면 true 로 켠다.
    auth_enabled: bool = False
    session_ttl_hours: int = 12
    log_level: str = "INFO"
    enable_scheduler: bool = True

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _mode(cls, v: object) -> object:
        if v is None or v == "":
            return "paper"
        m = str(v).strip().lower()
        if m not in ("paper", "live"):
            raise ValueError("TRADING_MODE 는 'paper'(모의투자) 또는 'live'(실거래)만 쓸 수 있습니다.")
        return m

    @model_validator(mode="after")
    def _live_needs_confirm(self) -> Settings:
        if self.trading_mode == "live" and self.live_trading_confirm.strip() != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                "TRADING_MODE=live 는 실제 돈으로 주문합니다. 켜려면 .env 에 "
                f"LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE} 를 함께 넣으세요."
            )
        return self

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @field_validator("llm_base_url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        return v.strip().rstrip("/")

    # --- 파생 값 ---------------------------------------------------------
    @property
    def has_toss(self) -> bool:
        return _filled(self.toss_client_id) and _filled(self.toss_client_secret)

    @property
    def has_dart(self) -> bool:
        return _filled(self.dart_api_key)

    @property
    def has_naver(self) -> bool:
        return _filled(self.naver_client_id) and _filled(self.naver_client_secret)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and _filled(self.llm_api_key))

    def require_toss(self) -> None:
        if not self.has_toss:
            raise ValueError(
                "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 필요합니다. 토스증권 WTS > 설정 > Open API 에서 발급해 .env 에 "
                "넣고, 같은 화면의 '허용 IP 관리'에 이 컴퓨터의 공인 IP 를 등록하세요."
            )

    def secret_values(self) -> list[str]:
        """로그 마스킹 대상 비밀값 목록."""
        out: list[str] = []
        for s in (
            self.toss_client_id,
            self.toss_client_secret,
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
