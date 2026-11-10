"""1단계 안전 수정: 멈춘 주문 닫기 · 판단 회차 유실 방지 · 스레드에서 부른 방송."""

from __future__ import annotations

import asyncio
import threading
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import Order
from app.engine.scheduler import BUSY_GRACE
from app.risk.rules import OrderIntent
from tests.fake_toss import at
from tests.helpers import make_container

D = date(2026, 11, 4)


def _statuses(c: Any) -> list[tuple[str, str | None]]:
    with c.db.session() as s:
        return [(o.status, o.reject_reason) for o in s.scalars(select(Order).order_by(Order.id))]


async def test_place_order_error_closes_order_instead_of_pending(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))

    async def boom(_req: Any) -> Any:
        raise RuntimeError("network down")

    real = c.engine.orders.broker.place_order
    c.engine.orders.broker.place_order = boom  # type: ignore[method-assign]
    order, decision = await c.engine.orders.submit(OrderIntent("005930", "BUY", 1, 70_000, "테스트"))
    assert decision.allowed and order is not None and order.status == "REJECTED"
    assert "주문 전송 실패(RuntimeError)" in (order.reject_reason or "")
    assert "005930" not in c.engine.orders.open_symbols()  # 같은 종목이 '중복'으로 막히지 않는다
    c.engine.orders.broker.place_order = real  # type: ignore[method-assign]
    again, _ = await c.engine.orders.submit(OrderIntent("005930", "BUY", 1, 70_000, "다시"))
    assert again is not None and again.status != "REJECTED"


async def test_stale_pending_orders_closed_at_boot(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    now = c.clock.now()
    with c.db.session() as s:
        s.add(
            Order(
                client_order_id="stale-1",
                symbol="005930",
                side="SELL",
                order_type="MARKET",
                quantity=3,
                status="PENDING",
                reason="손절",
                created_at=now,
                updated_at=now,
            )
        )
    assert "005930" in c.engine.orders.open_symbols()
    assert c.engine.orders.close_stale_pending() == 1
    assert _statuses(c)[-1][0] == "REJECTED" and "재시작" in (_statuses(c)[-1][1] or "")
    assert c.engine.orders.open_symbols() == set() and c.engine.orders.close_stale_pending() == 0


async def test_decision_waits_for_short_job_instead_of_skipping(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    sch = c.scheduler
    ran: list[str] = []

    async def decision() -> None:
        ran.append("hourly")

    await sch._busy.acquire()  # 시세 갱신 등 짧은 작업이 잠금을 잡고 있다
    task = asyncio.ensure_future(sch._guarded("hourly", decision, grace=BUSY_GRACE)())
    await asyncio.sleep(0.01)
    assert ran == []  # 건너뛰지 않고 기다린다
    sch._busy.release()
    await task
    assert ran == ["hourly"] and not sch._busy.locked()

    await sch._busy.acquire()  # grace 가 없는 작업은 예전처럼 건너뛴다
    await sch._guarded("refresh", decision)()
    assert ran == ["hourly"]
    sch._busy.release()


async def test_broadcast_from_worker_thread_goes_through_loop(tmp_path: Path) -> None:
    c = make_container(tmp_path, at(D, 10))
    q = c.hub.subscribe()
    t = threading.Thread(target=lambda: c.hub.broadcast({"type": "x", "n": 1}))
    t.start()
    t.join()
    msg = await asyncio.wait_for(q.get(), 1)  # 루프에서 깨어난다 (스레드에서 직접 넣으면 깨우지 못할 수 있다)
    assert msg == {"type": "x", "n": 1}
    c.hub.broadcast({"type": "y"})
    assert q.get_nowait() == {"type": "y"}
