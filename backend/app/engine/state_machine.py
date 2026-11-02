"""주문 상태 머신: PENDING → SUBMITTED → PARTIALLY_FILLED → FILLED / CANCELED / REJECTED."""

from __future__ import annotations

from app.brokers.base import OrderStatus as S

TRANSITIONS: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.SUBMITTED, S.REJECTED, S.CANCELED}),
    S.SUBMITTED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCELED, S.REJECTED}),
    S.PARTIALLY_FILLED: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCELED}),
    S.FILLED: frozenset(),
    S.CANCELED: frozenset(),
    S.REJECTED: frozenset(),
}


class InvalidTransitionError(ValueError):
    pass


def can_transition(cur: S, new: S) -> bool:
    return new in TRANSITIONS[cur]


def transition(cur: S, new: S) -> S:
    if cur == new and cur not in (S.PARTIALLY_FILLED,):
        return cur  # 같은 상태 재보고는 무시(멱등)
    if not can_transition(cur, new):
        raise InvalidTransitionError(f"{cur} → {new} 전이는 허용되지 않습니다")
    return new


def walk(cur: S, path: list[S]) -> S:
    for st in path:
        cur = transition(cur, st)
    return cur
