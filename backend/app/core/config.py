"""애플리케이션 설정.

비밀정보(데이터 API 키, LLM 키, APP_SECRET_KEY)는 .env 로만 주입하며 SecretStr 로 보관한다.
TRADING_MODE 기본은 "paper"(자체 모의계좌). "live"(토스증권 실계좌 주문)는 사용자가 .env 에서
LIVE_TRADING_CONFIRM 확인 문구까지 넣어야만 켜진다(D-34). 켜는 것은 사용자의 판단이다.
미장은 US_TRADING_MODE + US_LIVE_TRADING_CONFIRM 으로 국장과 따로 켠다(D-55).
시세는 토스증권 Open API(조회 전용)만 쓴다. 목업 데이터·가속 시뮬레이션은 없다(D-28).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.llm_providers import resolve_base_url

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
    # 뉴스 검색 키를 받은 곳: hub = 네이버 클라우드 NAVER API HUB, developers = 네이버 개발자센터 (D-44)
    naver_api: Literal["hub", "developers"] = "hub"
    # 구글 검색 (Gemini API 'Grounding with Google Search', D-49) — 유료(결제 등록 필요). 키가 비면 꺼짐
    gemini_api_key: SecretStr | None = None
    google_search_model: str = "gemini-3.5-flash-lite"
    google_search_max_per_session: int = Field(default=3, ge=1, le=20)  # 판단 세션 1회당 검색 상한 (비용)

    # --- LLM (OpenAI 호환 Chat Completions) -------------------------------
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_tool_mode: Literal["auto", "tools", "json"] = "auto"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_max_steps: int = Field(default=24, ge=2, le=80)
    llm_timeout_sec: float = 180.0
    # chat: /chat/completions | responses: OpenAI /responses (추론+도구) | auto: api.openai.com 이면 responses
    llm_api: Literal["auto", "chat", "responses"] = "auto"
    # 추론 강도 (none·minimal·low·medium·high·xhigh·max 중 모델이 지원하는 값). 비우면 모델 기본값
    llm_reasoning_effort: str = ""

    # --- 테스트용 LLM (D-42) ------------------------------------------------
    # 코드를 고친 뒤 실제 호출로 확인할 때만 쓰는 별도 엔드포인트. 앱(판단·답장)은 위 LLM_* 만 쓴다.
    # 비우면 점검 명령(./run.sh llm-check)이 실행되지 않는다. 실서비스 키로 점검하려면 --live 를 붙인다.
    llm_test_base_url: str = ""
    llm_test_model: str = ""
    llm_test_api_key: SecretStr | None = None
    llm_test_api: Literal["auto", "chat", "responses"] = "auto"
    llm_test_reasoning_effort: str = ""

    # --- 제공사별 LLM 키 (D-71) — 둘 다 넣어 두고 화면에서 제공사·모델을 바꾼다 ----------------
    # 비우면 그 제공사는 LLM_* 키가 그 제공사일 때만 쓸 수 있다
    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    # --- 요금 장부 공식 대조 (D-66) -------------------------------------------
    # OpenAI 조직 Usage·Costs API 는 Admin 키로만 조회된다. 비우면 대조하지 않는다 (LLM 호출에는 쓰지 않는다)
    openai_admin_key: SecretStr | None = None

    # --- 휴대폰 알림 (Web Push, D-39) ----------------------------------------
    # VAPID 개인키(P-256, base64url). ./run.sh 가 비어 있으면 만들어 .env 에 넣는다. 비우면 알림 꺼짐
    vapid_private_key: SecretStr | None = None
    # 푸시 서비스(애플·구글)가 문제 있을 때 연락할 곳. mailto:주소 또는 https://호스트 (경로 없이)
    vapid_subject: str = "https://github.com"

    # --- 판단 주기 --------------------------------------------------------
    # 장중 LLM 판단 간격(분). 60 → 10:00~15:00 매시 정각 (+ 08:35 장전). 0 → 14:00 한 번
    agent_interval_minutes: int = Field(default=60, ge=0, le=390)

    # --- 모의 계좌 -------------------------------------------------------
    paper_initial_cash: float = 10_000_000
    paper_commission_rate: float = 0.00015
    paper_sell_tax_rate: float = 0.0020
    paper_slippage_bps: float = 5.0
    paper_max_volume_participation: float = Field(default=0.05, gt=0, le=1)

    # --- 미장 (D-48, D-55) — 거래 모드는 국장(TRADING_MODE)과 따로 정한다 ---------------------
    us_market_enabled: bool = True
    # paper: 자체 달러 모의계좌 (기본) | live: 토스증권 실계좌로 미국 주식 실제 주문 (US_LIVE_TRADING_CONFIRM 필요)
    us_trading_mode: Literal["paper", "live"] = "paper"
    us_live_trading_confirm: str = ""  # live 일 때 LIVE_CONFIRM_PHRASE 와 같아야 함
    # 미장 실거래 하루 매수 총액 상한(달러). 리스크 한도와 별개로 코드가 강제한다
    us_live_daily_buy_limit: float = Field(default=3_000, gt=0)
    # 비우면 sqlite:///{DATA_DIR}/us_market.db (국장 DB 가 메모리면 미장도 메모리)
    us_database_url: str = ""
    paper_us_initial_cash: float = Field(default=10_000, gt=0)  # 달러
    paper_us_commission_rate: float = Field(default=0.001, ge=0, lt=0.05)
    paper_us_slippage_bps: float = Field(default=5.0, ge=0)
    # 장중 판단 간격(분). 비우면 AGENT_INTERVAL_MINUTES 와 같음. 0 → 정규장 중간 한 번
    us_agent_interval_minutes: int | None = Field(default=None, ge=0, le=390)

    # --- 앱 --------------------------------------------------------------
    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'app.db'}"
    data_dir: Path = BACKEND_DIR / "data"
    app_secret_key: SecretStr | None = None
    # 로컬 전용(127.0.0.1)이라 기본은 로그인 없음. 다른 사람이 접근할 수 있는 환경이면 true 로 켠다.
    auth_enabled: bool = False
    session_ttl_hours: int = 12
    log_level: str = "INFO"
    enable_scheduler: bool = True

    @field_validator("trading_mode", "us_trading_mode", mode="before")
    @classmethod
    def _mode(cls, v: object) -> object:
        if v is None or v == "":
            return "paper"
        m = str(v).strip().lower()
        if m not in ("paper", "live"):
            raise ValueError(
                "TRADING_MODE·US_TRADING_MODE 는 'paper'(모의투자) 또는 'live'(실거래)만 쓸 수 있습니다."
            )
        return m

    @field_validator("us_agent_interval_minutes", mode="before")
    @classmethod
    def _blank_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _live_needs_confirm(self) -> Settings:
        if self.trading_mode == "live" and self.live_trading_confirm.strip() != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                "TRADING_MODE=live 는 실제 돈으로 주문합니다. 켜려면 .env 에 "
                f"LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE} 를 함께 넣으세요."
            )
        if self.us_trading_mode == "live" and self.us_live_trading_confirm.strip() != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                "US_TRADING_MODE=live 는 미국 주식을 실제 돈으로 주문합니다. 켜려면 .env 에 "
                f"US_LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE} 를 함께 넣으세요."
            )
        return self

    @property
    def is_live(self) -> bool:
        """국장 실거래 여부."""
        return self.trading_mode == "live"

    @property
    def us_is_live(self) -> bool:
        """미장 실거래 여부 (미장이 꺼져 있으면 항상 False)."""
        return self.us_market_enabled and self.us_trading_mode == "live"

    @field_validator("llm_base_url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        return v.strip().rstrip("/")

    # --- 파생 값 ---------------------------------------------------------
    @property
    def us_interval_minutes(self) -> int:
        return (
            self.agent_interval_minutes
            if self.us_agent_interval_minutes is None
            else self.us_agent_interval_minutes
        )

    def us_db_url(self) -> str:
        """미장 DB 주소: US_DATABASE_URL → (국장이 메모리 DB 면 메모리) → data/us_market.db."""
        if self.us_database_url.strip():
            return self.us_database_url.strip()
        if self.database_url in ("sqlite://", "sqlite:///:memory:"):
            return "sqlite://"
        return f"sqlite:///{Path(self.data_dir) / 'us_market.db'}"

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
    def has_google_search(self) -> bool:
        return _filled(self.gemini_api_key) and bool(self.google_search_model.strip())

    @property
    def has_test_llm(self) -> bool:
        return bool(
            self.llm_test_base_url.strip() and self.llm_test_model.strip() and _filled(self.llm_test_api_key)
        )

    @property
    def has_push(self) -> bool:
        return _filled(self.vapid_private_key)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_paid_base_url and self.llm_model and _filled(self.llm_api_key))

    @property
    def llm_paid_base_url(self) -> str:
        """LLM_BASE_URL, 비어 있으면 키로 알아낸 공식 주소 (D-45)."""
        key = self.llm_api_key.get_secret_value() if self.llm_api_key else ""
        return resolve_base_url(self.llm_base_url, key)

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
            self.vapid_private_key,
            self.llm_test_api_key,
            self.gemini_api_key,
            self.openai_admin_key,
            self.openai_api_key,
            self.openrouter_api_key,
        ):
            if _filled(s):
                assert s is not None
                out.append(s.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
