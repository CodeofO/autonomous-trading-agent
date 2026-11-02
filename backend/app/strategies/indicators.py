"""기술 지표 (순수 함수)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def sma(values: Sequence[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder RSI."""
    if len(closes) < n + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def pct_change(values: Sequence[float], n: int) -> float | None:
    if len(values) <= n or values[-n - 1] == 0:
        return None
    return values[-1] / values[-n - 1] - 1


def volatility(closes: Sequence[float], n: int = 20) -> float | None:
    if len(closes) < n + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / n
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0


def max_drawdown(equity: Sequence[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd
