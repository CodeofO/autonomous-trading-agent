"""매매 내역 원장: 체결 기록을 순서대로 다시 계산해 매도별 실현손익과 매매 사유를 만든다.

모의계좌(PaperBroker._apply_fill)와 같은 평균단가 방식이다.
- 매수: 평단 = (기존 평단 × 수량 + 체결금액 + 수수료) / 새 수량
- 매도: 실현손익 = (체결가 − 평단) × 수량 − 수수료 − 세금

계산 결과는 DB·기준 시각(since)마다 캐시한다(B2). 캐시 키는 체결 기록의 (건수, 마지막 id) — 조회 1번으로 확인한다.
새 체결이 기존 마지막 체결 뒤에 붙었으면 그 체결만 이어서 계산하고, 그 밖(삭제·순서가 앞선 체결)이면 처음부터 다시
계산한다. 종목 이름은 결과를 만들 때 붙인다(이름 함수는 캐시된 이름을 돌려준다).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import func, select

from app.core.clock import KST
from app.db.models import Fill, Order
from app.db.session import Database


@dataclass
class _Replay:
    """체결을 처음부터 순서대로 다시 계산한 상태 (이름 없이)."""

    count: int = 0
    last_id: int = 0
    last_at: datetime | None = None
    pos: dict[str, tuple[int, float]] = field(default_factory=dict)  # symbol → (수량, 평단)
    trades: list[dict[str, Any]] = field(default_factory=list)  # 시간순, "name" 은 빈 칸
    realized: list[tuple[str, float]] = field(default_factory=list)  # 시간순 (symbol, 실현손익)
    total: float = 0.0
    fees: float = 0.0
    taxes: float = 0.0

    def add(self, f: Fill, o: Order) -> None:
        self.count += 1
        self.last_id = max(self.last_id, f.id)
        self.last_at = f.filled_at
        self.fees += f.fee
        self.taxes += f.tax
        qty, avg = self.pos.get(f.symbol, (0, 0.0))
        t: dict[str, Any] = {
            "time": f.filled_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "symbol": f.symbol,
            "name": "",
            "side": f.side,
            "quantity": f.quantity,
            "price": f.price,  # 반올림은 결과를 만들 때 (통화별)
            "reason": o.reason or "",
            "by": "에이전트 판단" if o.run_id else "자동(손절·익절 등)",
        }
        if o.run_id:
            t["session"] = o.run_id
        if f.side == "BUY":
            new_qty = qty + f.quantity
            self.pos[f.symbol] = (new_qty, (avg * qty + f.price * f.quantity + f.fee) / new_qty)
        elif qty < f.quantity:
            # 이 시스템 밖에서 산 주식(실계좌 기존 보유분 등)이 섞인 매도 → 평단을 몰라 계산하지 않는다
            t["realized_pnl"] = None
            t["note"] = "매수 기록이 없어 실현손익을 계산할 수 없음 (시스템 밖 보유분)"
            self.pos.pop(f.symbol, None)
        else:
            pnl = (f.price - avg) * f.quantity - f.fee - f.tax
            t["avg_cost"] = avg
            t["realized_pnl"] = pnl
            self.total += pnl
            self.realized.append((f.symbol, pnl))
            left = qty - f.quantity
            if left > 0:
                self.pos[f.symbol] = (left, avg)
            else:
                self.pos.pop(f.symbol, None)
        self.trades.append(t)


_CACHE: WeakKeyDictionary[Database, dict[datetime | None, _Replay]] = WeakKeyDictionary()
_LOCK = threading.Lock()


def _fills(db: Database, since: datetime | None, after_id: int = 0) -> list[tuple[Fill, Order]]:
    q = select(Fill, Order).join(Order, Fill.order_id == Order.id).order_by(Fill.filled_at, Fill.id)
    if since is not None:
        q = q.where(Fill.filled_at >= since)
    if after_id:
        q = q.where(Fill.id > after_id)
    with db.session() as s:
        return list(s.execute(q).tuples())


def _replay(db: Database, since: datetime | None) -> _Replay:
    """캐시된 계산 상태를 최신으로 맞춰 돌려준다 (호출자가 _LOCK 을 잡고 있어야 한다)."""
    q = select(func.count(Fill.id), func.max(Fill.id))
    if since is not None:
        q = q.where(Fill.filled_at >= since)
    with db.session() as s:
        count, last_id = s.execute(q).one()
    count, last_id = int(count or 0), int(last_id or 0)
    per_db = _CACHE.setdefault(db, {})
    st = per_db.get(since)
    if st is not None and (st.count, st.last_id) == (count, last_id):
        return st
    if st is not None and count > st.count and last_id > st.last_id:
        new = _fills(db, since, st.last_id)
        in_order = st.last_at is None or all(f.filled_at >= st.last_at for f, _ in new)
        if in_order and st.count + len(new) == count:  # 뒤에 붙은 체결만 → 이어서 계산
            for f, o in new:
                st.add(f, o)
            return st
    st = _Replay()
    for f, o in _fills(db, since):
        st.add(f, o)
    if len(per_db) >= 4:  # 기준 시각은 계좌 전환 때만 바뀐다 → 몇 개만 남긴다
        per_db.clear()
    per_db[since] = st
    return st


def clear_cache(db: Database | None = None) -> None:
    with _LOCK:
        if db is None:
            _CACHE.clear()
        else:
            _CACHE.pop(db, None)


def trade_ledger(
    db: Database,
    names: Callable[[str], str] | None = None,
    since: datetime | None = None,
    limit: int = 30,
    currency: str = "KRW",
) -> dict[str, Any]:
    """최근 체결 내역(최신순, 최대 limit 건)과 종목별·전체 실현손익.

    currency=USD(미장)이면 금액을 센트까지 남긴다. 시각은 두 시장 모두 한국 시각(KST)으로 표기한다.
    """
    digits = 2 if currency == "USD" else None

    def r(v: float) -> float:
        return round(v, digits) if digits else round(v)

    def name_of(sym: str) -> str:
        return (names(sym) if names else "") or sym

    with _LOCK:
        st = _replay(db, since)
        recent = list(reversed(st.trades))[:limit]
        realized = list(st.realized)
        total, fees, taxes, n = st.total, st.fees, st.taxes, len(st.trades)

    by_name: dict[str, float] = {}
    for sym, pnl in realized:  # 시간순으로 더한다 (예전과 같은 합산 순서)
        nm = name_of(sym)
        by_name[nm] = by_name.get(nm, 0.0) + pnl
    out_trades = []
    for t in recent:
        row = {**t, "name": name_of(t["symbol"]), "price": r(t["price"])}
        if "avg_cost" in row:
            row["avg_cost"] = r(row["avg_cost"])
        if row.get("realized_pnl") is not None:
            row["realized_pnl"] = r(row["realized_pnl"])
        out_trades.append(row)
    out: dict[str, Any] = {
        "realized_pnl_total": r(total),
        "realized_pnl_by_stock": {k: r(v) for k, v in by_name.items()},
        "fees_total": r(fees),
        "taxes_total": r(taxes),
        "fill_count": n,
        "recent_fills": out_trades,
    }
    if currency != "KRW":
        out["currency"] = currency
    return out
