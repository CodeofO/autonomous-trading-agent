"""규칙 기반 예시 전략 (플러그인). LLM 이 없을 때의 대체 판단과 백테스트에 쓴다.

※ 예시일 뿐이며 투자 권유가 아니다.
"""

from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, Field, model_validator

from app.market.types import Bar
from app.strategies.indicators import rsi, sma


@dataclass(frozen=True)
class Signal:
    action: Literal["BUY", "SELL"]
    reason: str
    strength: float = 0.5


class Strategy(ABC):
    type: ClassVar[str]
    label: ClassVar[str]
    Params: ClassVar[builtins.type[BaseModel]]

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = self.Params(**(params or {}))

    @abstractmethod
    def evaluate(self, bars: Sequence[Bar]) -> Signal | None: ...


class MaCrossParams(BaseModel):
    short: int = Field(5, ge=2, le=60)
    long: int = Field(20, ge=3, le=250)

    @model_validator(mode="after")
    def _order(self) -> MaCrossParams:
        if self.short >= self.long:
            raise ValueError("short 는 long 보다 작아야 합니다")
        return self


class MovingAverageCross(Strategy):
    type = "ma_cross"
    label = "이동평균 골든/데드 크로스"
    Params = MaCrossParams

    def evaluate(self, bars: Sequence[Bar]) -> Signal | None:
        p = cast(MaCrossParams, self.params)
        closes = [b.close for b in bars]
        if len(closes) < p.long + 1:
            return None
        s_now, l_now = sma(closes, p.short), sma(closes, p.long)
        s_prev, l_prev = sma(closes[:-1], p.short), sma(closes[:-1], p.long)
        assert s_now is not None and l_now is not None and s_prev is not None and l_prev is not None
        if s_prev <= l_prev and s_now > l_now:
            return Signal("BUY", f"골든크로스 MA{p.short}({s_now:,.0f}) > MA{p.long}({l_now:,.0f})", 0.6)
        if s_prev >= l_prev and s_now < l_now:
            return Signal("SELL", f"데드크로스 MA{p.short}({s_now:,.0f}) < MA{p.long}({l_now:,.0f})", 0.6)
        return None


class RsiParams(BaseModel):
    period: int = Field(14, ge=2, le=100)
    lower: float = Field(30, ge=1, le=99)
    upper: float = Field(70, ge=1, le=99)

    @model_validator(mode="after")
    def _order(self) -> RsiParams:
        if self.lower >= self.upper:
            raise ValueError("lower 는 upper 보다 작아야 합니다")
        return self


class RsiReversal(Strategy):
    type = "rsi"
    label = "RSI 역추세"
    Params = RsiParams

    def evaluate(self, bars: Sequence[Bar]) -> Signal | None:
        p = cast(RsiParams, self.params)
        v = rsi([b.close for b in bars], p.period)
        if v is None:
            return None
        if v < p.lower:
            return Signal("BUY", f"RSI {v:.1f} < {p.lower:g} (과매도)", 0.55)
        if v > p.upper:
            return Signal("SELL", f"RSI {v:.1f} > {p.upper:g} (과매수)", 0.55)
        return None


REGISTRY: dict[str, builtins.type[Strategy]] = {s.type: s for s in (MovingAverageCross, RsiReversal)}


def build(type_: str, params: dict[str, Any] | None = None) -> Strategy:
    if type_ not in REGISTRY:
        raise KeyError(f"알 수 없는 전략: {type_}")
    return REGISTRY[type_](params)


def param_schema(type_: str) -> dict[str, Any]:
    return REGISTRY[type_].Params.model_json_schema()
