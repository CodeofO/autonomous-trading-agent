"""DB 모델 (SQLAlchemy 2 typed ORM)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """항상 UTC 로 저장하고 aware(UTC) 로 돌려준다.

    SQLite 는 시간대 정보를 버리므로, KST 시각을 그대로 넣으면 읽을 때 어긋난다(D-14).
    naive 값이 들어오면 UTC 로 간주한다.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AppSetting(Base):
    """키-값 설정 저장소 (risk 설정, 엔진 상태 등)."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class StrategyConfig(Base):
    __tablename__ = "strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    symbols: Mapped[list[Any]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DailyPrice(Base):
    """일봉 + 시가총액 (토스증권 캔들 + 종목 발행주식수)."""

    __tablename__ = "daily_prices"
    # symbol 단독 인덱스는 두지 않는다 — uq_daily_price(symbol, day) 가 symbol 조회를 같이 맡는다 (0004)
    __table_args__ = (UniqueConstraint("symbol", "day", name="uq_daily_price"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    day: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    market: Mapped[str] = mapped_column(String(8), default="")
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    market_cap: Mapped[float] = mapped_column(Float, default=0.0)
    listed_shares: Mapped[float] = mapped_column(Float, default=0.0)


class CorpCode(Base):
    """OpenDART 고유번호 ↔ 종목코드 매핑."""

    __tablename__ = "corp_codes"
    stock_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    corp_code: Mapped[str] = mapped_column(String(8))
    corp_name: Mapped[str] = mapped_column(String(128))


class ApiCache(Base):
    """외부 API 응답 캐시 (재무제표 등 자주 바뀌지 않는 데이터)."""

    __tablename__ = "api_cache"
    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    fill_basis: Mapped[str] = mapped_column(String(8), default="")  # OPEN | CLOSE
    trade_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
    reject_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    type: Mapped[str] = mapped_column(String(24), index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PaperAccountState(Base):
    """PaperBroker 가상 계좌 스냅샷 (단일 행)."""

    __tablename__ = "paper_account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)


class AgentRun(Base):
    """에이전트 판단 세션 1회."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_status_id", "status", "id"),)  # 완료 세션 최근 N개 (기억)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))  # llm-tools | llm-json | rule
    model: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    market_view: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(8))  # BUY | SELL | HOLD
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str] = mapped_column(String(8), default="MARKET")
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class ChatMessage(Base):
    """트레이딩 룸 채팅 피드. 에이전트 사고 과정·리스크 차단·체결 소식·사용자 메시지."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_role_id", "role", "id"),)  # 내 메시지 최근 N개 (기억)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # analyst | risk | broker | system | user
    kind: Mapped[str] = mapped_column(String(16))  # text | tool | decision | fill | block | summary
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MemoryCard(Base):
    """메모 카드 (D-52): 사용자가 직접 관리하는 매니저의 기억 — 관심·주목·피할 종목, 운용 지시."""

    __tablename__ = "memory_cards"
    __table_args__ = (Index("ix_memory_cards_symbol_kind", "symbol", "kind"),)  # 같은 카드 중복 확인
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # watch | focus | avoid | note
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # 그 시장 현지 날짜, 그날까지 유효
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MemoryTopic(Base):
    """대화에서 자라는 기억 (D-67): 투자자가 트레이딩 룸에서 꺼낸 주제 하나 — 종목·업종·테마·운용 지시.

    중요도는 저장하지 않고 읽을 때 날짜로 다시 계산한다(app/agent/topics.py): 여러 날 되풀이하면 오르고,
    오래 안 꺼내면 줄고, 매니저(LLM)가 새 메시지를 볼 때 올림/유지/내림/잊기로 고친다.
    """

    __tablename__ = "memory_topics"
    __table_args__ = (UniqueConstraint("key", name="uq_memory_topics_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80))  # sym:<종목코드> | t:<정규화한 이름>
    label: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 언급한 시장 현지 날짜 "YYYY-MM-DD" (최근 30일)
    days: Mapped[list[Any]] = mapped_column(JSON, default=list)
    mentions: Mapped[int] = mapped_column(Integer, default=0)  # 언급한 메시지 수
    quotes: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 최근 인용 3개 {id, at, text}
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    llm_level: Mapped[int] = mapped_column(Integer, default=0)  # 매니저 판단: 1 올림 · 0 유지 · -1 내림
    llm_reason: Mapped[str] = mapped_column(Text, default="")
    llm_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # '잊어줘' — 이후 다시 꺼낼 때까지 중요도 0
    forgot_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    pin: Mapped[str] = mapped_column(String(8), default="")  # '' 자동 | pin 늘 기억 | mute 빼기


class LlmUsage(Base):
    """LLM 호출 1회(또는 예전 세션 합계)의 토큰 사용량과 실제 요금 — 요금 장부 (D-41, D-66).

    모든 시장·실험실 계좌의 기록은 국장 DB 한 곳에 모은다(market·strategy 로 구분).
    cached_tokens 는 예전(D-41) '바로 전 호출 입력과 겹치는 앞부분' 추정치, cache_read_tokens 는
    제공사가 응답으로 알려 준 실제 캐시 적중 입력이다(알려 주지 않으면 None).
    """

    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # session | chat
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    calls: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    max_input_tokens: Mapped[int] = mapped_column(
        Integer, default=0
    )  # 호출 1회 최대 입력 (장문 요금 구간 판단)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | backfill
    # ---- D-66 요금 장부
    market: Mapped[str] = mapped_column(String(8), default="", server_default="")  # KR | US | '' (공용)
    # '' 주 계좌 | 실험실 전략 id
    strategy: Mapped[str] = mapped_column(String(24), default="", server_default="")
    profile: Mapped[str] = mapped_column(String(8), default="", server_default="")  # free | paid
    # openai | google | openrouter | local(자체 서버) | gateway(알 수 없는 유료 주소)
    provider: Mapped[str] = mapped_column(String(16), default="", server_default="")
    served_model: Mapped[str] = mapped_column(String(128), default="", server_default="")
    upstream: Mapped[str] = mapped_column(String(64), default="", server_default="")  # OpenRouter 공급사
    response_id: Mapped[str] = mapped_column(String(128), default="", server_default="")
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # reported | priced | free | unpriced | estimated
    cost_source: Mapped[str] = mapped_column(String(12), default="", server_default="")
    origin: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)


class DailyReport(Base):
    __tablename__ = "daily_reports"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class NewsEdition(Base):
    """뉴스 룸 한 판 (D-56): 매일 07·12·17시(한국 시각) 또는 수동 새로고침으로 모은 섹션별 뉴스와 요약.

    payload = {trigger, slot, headline, sections[{key, title, icon, summary, items[], errors[]}], stats, …}.
    국장 DB 에만 쌓인다(뉴스 룸은 시장과 관계없는 공용 화면). 오래된 판은 지운다(KEEP_DAYS).
    """

    __tablename__ = "news_editions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
