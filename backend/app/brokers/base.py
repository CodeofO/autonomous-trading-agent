"""브로커 인터페이스. 엔진은 이 인터페이스만 안다."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    day: date  # 가격 기준일(공식 종가일)
    name: str = ""


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None


@dataclass(frozen=True)
class FillInfo:
    quantity: int
    price: float
    fee: float
    tax: float
    filled_at: datetime


@dataclass
class OrderResult:
    broker_order_id: str
    client_order_id: str
    status: OrderStatus
    filled_qty: int = 0
    avg_fill_price: float | None = None
    fills: list[FillInfo] = field(default_factory=list)
    reject_reason: str | None = None
    fill_basis: str = ""
    trade_day: date | None = None
    # 이번 갱신에서 거친 중간 상태 (예: PARTIALLY_FILLED → CANCELED)
    path: list[OrderStatus] = field(default_factory=list)


@dataclass(frozen=True)
class Position:
    symbol: str
    name: str
    quantity: int
    avg_price: float
    last_price: float
    price_day: date | None
    opened_day: date | None

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.avg_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        return (self.last_price / self.avg_price - 1) if self.avg_price else 0.0


@dataclass(frozen=True)
class Balance:
    cash: float
    reserved_cash: float
    positions_value: float
    equity: float
    initial_cash: float
    realized_pnl: float
    fees_paid: float
    taxes_paid: float

    @property
    def available_cash(self) -> float:
        return self.cash - self.reserved_cash


class BrokerInterface(ABC):
    #: True 인 브로커만 주문을 받는다 (실거래 브로커 구현은 존재하지 않음 — D-01)
    is_paper: bool = False

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote | None: ...

    @abstractmethod
    async def get_candles(self, symbol: str, n: int = 120) -> list[object]: ...

    @abstractmethod
    async def get_balance(self) -> Balance: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> OrderResult: ...

    @abstractmethod
    async def get_order_status(self, broker_order_id: str) -> OrderResult: ...
