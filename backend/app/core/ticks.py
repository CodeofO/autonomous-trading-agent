"""KRX 호가 단위(2023년 개편 기준, 코스피·코스닥 공통)."""

from __future__ import annotations

import math

_TABLE: list[tuple[float, int]] = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
]


def tick_size(price: float) -> int:
    for bound, tick in _TABLE:
        if price < bound:
            return tick
    return 1_000


def round_to_tick(price: float, mode: str = "nearest") -> float:
    t = tick_size(price)
    q = price / t
    if mode == "up":
        n = math.ceil(q - 1e-9)
    elif mode == "down":
        n = math.floor(q + 1e-9)
    else:
        n = round(q)
    return float(max(n, 1) * t)
