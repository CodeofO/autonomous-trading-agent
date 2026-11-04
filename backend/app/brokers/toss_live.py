"""토스증권 실계좌 브로커 (TRADING_MODE=live 에서만 생성, D-34).

근거: 토스증권 Open API 명세 openapi.json 1.2.28 (docs/TOSS_API_NOTES.md §주문·계좌)
- 계좌: GET /api/v1/accounts → accountSeq. 계좌·주문 API 는 `X-Tossinvest-Account: {accountSeq}` 헤더 필요
- 잔고: GET /api/v1/holdings (items[].symbol·quantity·averagePurchasePrice·lastPrice), GET /api/v1/buying-power
- 주문: POST /api/v1/orders {clientOrderId, symbol, side, orderType, timeInForce, quantity, price}
        → {orderId}. 숫자는 문자열(decimal). 1억원 이상은 confirmHighValueOrder 필요(이 시스템은 보내지 않음)
- 취소: POST /api/v1/orders/{orderId}/cancel → {orderId}(취소 요청의 새 번호)
- 조회: GET /api/v1/orders/{orderId} → status, execution{filledQuantity, averageFilledPrice, commission, tax}
        주문 1건 = 1행(부분체결은 누적 합산) → 체결 증분은 OrderManager 가 DB 기록과의 차이로 계산한다
- 토큰은 클라이언트당 1개만 유효 → 시세용 TossClient 의 토큰을 같이 쓴다

안전장치: 생성 시 trading_mode=live 확인, 하루 매수 총액 상한, 1억원 이상 주문 거부,
주문 POST 무재시도(SafeHttpClient), 응답 불명 시 같은 clientOrderId 로만 재확인(멱등키 10분).
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import httpx

from app.brokers.base import (
    Balance,
    BrokerInterface,
    FillInfo,
    OrderRequest,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Quote,
)
from app.brokers.paper import PaperCosts
from app.core.clock import KST, Clock
from app.core.http import LiveOrderBlockedError, Route, SafeHttpClient, TokenBucket
from app.engine.settings_store import SettingsStore
from app.market.store import PriceStore
from app.market.toss import BASE, HOST, TossApiError, TossClient, TossMarketProvider, _num

log = logging.getLogger(__name__)

TRADING_ROUTES = [
    Route(HOST, "GET", re.compile(r"/api/v1/accounts")),
    Route(HOST, "GET", re.compile(r"/api/v1/holdings")),
    Route(HOST, "GET", re.compile(r"/api/v1/buying-power")),
    Route(HOST, "GET", re.compile(r"/api/v1/sellable-quantity")),
    Route(HOST, "POST", re.compile(r"/api/v1/orders")),
    Route(HOST, "GET", re.compile(r"/api/v1/orders/[A-Za-z0-9\-_]+")),
    Route(HOST, "POST", re.compile(r"/api/v1/orders/[A-Za-z0-9\-_]+/cancel")),
]
# 공식 Rate Limits 의 80% (ACCOUNT 1, ASSET 5, ORDER 10, ORDER_HISTORY 5, ORDER_INFO 6 /s)
RATE_GROUPS = {"ACCOUNT": 0.8, "ASSET": 4, "ORDER": 8, "ORDER_HISTORY": 4, "ORDER_INFO": 2.4}
HIGH_VALUE_ORDER = 100_000_000  # 이 금액 이상은 토스가 별도 확인을 요구 → 이 시스템은 아예 보내지 않는다

# 토스 주문 상태 → 내부 상태
_STATUS = {
    "PENDING": OrderStatus.SUBMITTED,
    "PENDING_REPLACE": OrderStatus.SUBMITTED,
    "PENDING_CANCEL": OrderStatus.SUBMITTED,
    "PARTIAL_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "REPLACED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
}


def krx_tick(price: float) -> int:
    """국내 주식 호가단위 (KRX, 2023-01-25 이후 유가·코스닥 공통)."""
    for bound, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < bound:
            return tick
    return 1_000


def round_to_tick(price: float, side: OrderSide) -> int:
    """지정가를 호가단위에 맞춘다: 매수는 내림, 매도는 올림 (불리한 방향으로 넘지 않게)."""
    tick = krx_tick(price)
    f = math.floor if side == OrderSide.BUY else math.ceil
    return int(f(price / tick) * tick)


class TossTradingClient:
    """주문·계좌 API 전용 클라이언트. 토큰은 시세 클라이언트(TossClient)와 공유한다."""

    def __init__(self, toss: TossClient, *, http: SafeHttpClient | None = None) -> None:
        self.toss = toss
        self.http = http or SafeHttpClient(TRADING_ROUTES, rate_per_sec=20, max_retries=3, allow_trading=True)
        self._buckets = {g: TokenBucket(rate=r, capacity=max(1.0, r)) for g, r in RATE_GROUPS.items()}
        self.account_seq: str = ""

    async def _call(
        self,
        group: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        account: bool = True,
    ) -> Any:
        if account and not self.account_seq:
            raise TossApiError(400, "account-header-required", "주문 계좌(accountSeq)가 정해지지 않았어요")
        for attempt in (0, 1):
            token = await self.toss._bearer(force=attempt == 1)
            await self._buckets[group].acquire()
            headers = {"Authorization": f"Bearer {token}"}
            if account:
                headers["X-Tossinvest-Account"] = self.account_seq
            r = await self.http.request(method, f"{BASE}{path}", params=params, json=body, headers=headers)
            if 200 <= r.status_code < 300:
                return r.json().get("result")
            code, message = "", r.text[:200]
            data: dict[str, Any] = {}
            try:
                err = r.json().get("error") or {}
                code, message, data = (
                    str(err.get("code", "")),
                    str(err.get("message", "")),
                    err.get("data") or {},
                )
            except ValueError:
                pass
            # 401 은 처리되지 않은 요청이라 새 토큰으로 한 번 더 보내도 안전하다
            if (
                r.status_code == 401
                and attempt == 0
                and code in ("invalid-token", "expired-token", "token-revoked")
            ):
                continue
            e = TossApiError(r.status_code, code, message)
            e.data = data  # type: ignore[attr-defined]
            raise e
        raise TossApiError(401, "token", "토큰 재발급 후에도 인증 실패")

    async def accounts(self) -> list[dict[str, Any]]:
        return list(await self._call("ACCOUNT", "GET", "/api/v1/accounts", account=False) or [])

    async def holdings(self) -> dict[str, Any]:
        return dict(await self._call("ASSET", "GET", "/api/v1/holdings") or {})

    async def buying_power(self) -> float:
        res = await self._call("ORDER_INFO", "GET", "/api/v1/buying-power", params={"currency": "KRW"})
        return _num((res or {}).get("cashBuyingPower"))

    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._call("ORDER", "POST", "/api/v1/orders", body=body) or {})

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return dict(await self._call("ORDER", "POST", f"/api/v1/orders/{order_id}/cancel", body={}) or {})

    async def order(self, order_id: str) -> dict[str, Any]:
        return dict(await self._call("ORDER_HISTORY", "GET", f"/api/v1/orders/{order_id}") or {})

    async def aclose(self) -> None:
        await self.http.aclose()


class TossLiveBroker(BrokerInterface):
    """토스증권 실계좌로 실제 주문을 내는 브로커."""

    is_paper = False
    is_live = True

    def __init__(
        self,
        client: TossTradingClient,
        quotes: TossMarketProvider,
        store: PriceStore,
        clock: Clock,
        kv: SettingsStore,
        *,
        trading_mode: str,
        daily_buy_limit: float,
        account_seq: str = "",
        costs: PaperCosts | None = None,
        ledger: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        if trading_mode != "live":
            raise LiveOrderBlockedError("실계좌 브로커는 TRADING_MODE=live 에서만 만들 수 있어요.")
        self.client = client
        self.quotes = quotes
        self.store = store
        self.clock = clock
        self.kv = kv
        self.trading_mode = trading_mode
        self.daily_buy_limit = daily_buy_limit
        self.costs = costs or PaperCosts()  # 백테스트 비용 가정용 (실거래 비용은 토스 체결 내역 기준)
        self.ledger = ledger  # 실현손익·수수료 (DB 체결 기록 기준)
        if account_seq:
            client.account_seq = account_seq.strip()

    # ----------------------------------------------------------- 계좌
    async def ensure_account(self) -> str:
        """주문 계좌 확정: .env 의 TOSS_ACCOUNT_SEQ, 없으면 종합(BROKERAGE) 계좌가 하나일 때 그 계좌."""
        if self.client.account_seq:
            return self.client.account_seq
        accounts = [a for a in await self.client.accounts() if a.get("accountType") == "BROKERAGE"]
        if len(accounts) != 1:
            raise TossApiError(
                400,
                "account-ambiguous",
                f"종합 계좌가 {len(accounts)}개예요. .env 의 TOSS_ACCOUNT_SEQ 에 주문할 계좌의 accountSeq 를 넣어 주세요.",
            )
        self.client.account_seq = str(accounts[0]["accountSeq"])
        return self.client.account_seq

    def _guard(self) -> None:
        if self.trading_mode != "live" or not self.is_live:
            raise LiveOrderBlockedError("실거래 모드가 아니라 주문을 보낼 수 없어요.")

    # ----------------------------------------------------------- 조회
    async def get_quote(self, symbol: str) -> Quote | None:
        px = (await self.quotes.last_prices([symbol])).get(symbol)
        if px:  # 이름 = 마지막 일봉의 이름 (캐시)
            return Quote(symbol, px, self.clock.now().astimezone(KST).date(), self.store.name_of(symbol))
        b = self.store.last_bar(symbol)
        return Quote(symbol, b.close, b.day, b.name) if b else None

    async def get_candles(self, symbol: str, n: int = 120) -> list[object]:
        return list(self.store.history(symbol, n=n))

    async def get_positions(self) -> list[Position]:
        await self.ensure_account()
        h = await self.client.holdings()
        today = self.clock.now().astimezone(KST).date()
        opened = self.kv.get("live_opened")
        out = []
        for it in h.get("items") or []:
            qty = int(_num(it.get("quantity")))
            if it.get("marketCountry") not in (None, "KR") or qty <= 0:
                continue
            sym = str(it["symbol"])
            out.append(
                Position(
                    symbol=sym,
                    name=str(it.get("name") or self.store.name_of(sym)),
                    quantity=qty,
                    avg_price=_num(it.get("averagePurchasePrice")),
                    last_price=_num(it.get("lastPrice")),
                    price_day=today,
                    opened_day=date.fromisoformat(opened[sym]) if sym in opened else None,
                )
            )
        return out

    async def get_balance(self) -> Balance:
        return await self._balance(await self.get_positions())

    async def account(self) -> tuple[Balance, list[Position]]:
        positions = await self.get_positions()  # 토스 잔고 조회 1번으로 잔고·보유 종목을 같이
        return await self._balance(positions), positions

    async def _balance(self, positions: list[Position]) -> Balance:
        cash = await self.client.buying_power()
        pv = sum(p.market_value for p in positions)
        start = self.kv.get("live_account")
        if not start.get("initial_equity"):  # 실거래 시작 시점 평가액 = 수익률 기준
            start = {"initial_equity": cash + pv, "since": self.clock.now().isoformat()}
            self.kv.set("live_account", start)
        led = self.ledger() if self.ledger else {}
        return Balance(
            cash=cash,
            reserved_cash=0.0,  # 토스 주문가능금액이 이미 미체결 매수분을 뺀 값
            positions_value=pv,
            equity=cash + pv,
            initial_cash=float(start["initial_equity"]),
            realized_pnl=float(led.get("realized_pnl_total", 0.0)),
            fees_paid=float(led.get("fees_total", 0.0)),
            taxes_paid=float(led.get("taxes_total", 0.0)),
        )

    # ----------------------------------------------------------- 주문
    def _daily_bought(self) -> float:
        d = self.kv.get("live_daily_buys")
        return float(d.get("amount", 0.0)) if d.get("day") == self._today() else 0.0

    def _today(self) -> str:
        return self.clock.now().astimezone(KST).date().isoformat()

    def _rejected(self, req: OrderRequest, code: str) -> OrderResult:
        return OrderResult(
            "", req.client_order_id, OrderStatus.REJECTED, reject_reason=code, path=[OrderStatus.REJECTED]
        )

    async def place_order(self, req: OrderRequest) -> OrderResult:
        self._guard()
        await self.ensure_account()
        price: int | None = None
        if req.order_type == OrderType.LIMIT:
            if not req.limit_price or req.limit_price <= 0:
                return self._rejected(req, "invalid-limit-price")
            price = round_to_tick(req.limit_price, req.side)
        ref = price or (q.price if (q := await self.get_quote(req.symbol)) else 0.0)
        notional = req.quantity * ref
        if notional >= HIGH_VALUE_ORDER:
            return self._rejected(req, "high-value-order-blocked")
        if req.side == OrderSide.BUY and self._daily_bought() + notional > self.daily_buy_limit:
            return self._rejected(req, "live-daily-buy-limit")

        body: dict[str, Any] = {
            "clientOrderId": req.client_order_id[:36],
            "symbol": req.symbol,
            "side": req.side.value,
            "orderType": req.order_type.value,
            "timeInForce": "DAY",
            "quantity": str(req.quantity),
        }
        if price is not None:
            body["price"] = str(price)

        res: dict[str, Any] | None = None
        for attempt in range(3):
            try:
                res = await self.client.create_order(body)
                break
            except TossApiError as e:
                if e.status >= 500 or e.code == "request-in-progress":
                    await asyncio.sleep(1.0 + attempt)  # 같은 clientOrderId 로 재확인 (멱등키 10분)
                    continue
                log.warning("실주문 거부 %s %s: %s", req.symbol, e.code, e)
                return self._rejected(req, e.code or f"http-{e.status}")
            except (httpx.TransportError, httpx.TimeoutException):
                await asyncio.sleep(1.0 + attempt)
        if res is None or not res.get("orderId"):
            # 접수됐는지 알 수 없다 → 주문 기록은 거부로 두고 사용자에게 토스 앱 확인을 요청 (OrderManager 채팅)
            return self._rejected(req, "unknown-submit-state")

        if req.side == OrderSide.BUY:
            self.kv.set("live_daily_buys", {"day": self._today(), "amount": self._daily_bought() + notional})
        oid = str(res["orderId"])
        try:
            result = await self.get_order_status(oid)
        except Exception as e:  # 접수는 됐다 → 체결 확인은 1분 주기 동기화에 맡긴다
            log.warning("주문 상태 조회 실패 %s: %s", oid, e)
            result = OrderResult(
                oid, req.client_order_id, OrderStatus.SUBMITTED, path=[OrderStatus.SUBMITTED]
            )
        result.client_order_id = req.client_order_id
        if OrderStatus.SUBMITTED not in result.path:
            result.path = [OrderStatus.SUBMITTED, *result.path]  # PENDING → SUBMITTED → (체결)
        return result

    async def cancel_order(self, broker_order_id: str) -> OrderResult:
        self._guard()
        await self.ensure_account()
        try:
            await self.client.cancel_order(broker_order_id)
        except TossApiError as e:
            if e.code not in ("already-filled", "already-canceled", "already-rejected", "already-processing"):
                raise
        return await self.get_order_status(broker_order_id)

    async def get_order_status(self, broker_order_id: str) -> OrderResult:
        await self.ensure_account()
        o = await self.client.order(broker_order_id)
        status = _STATUS.get(str(o.get("status")), OrderStatus.SUBMITTED)
        ex = o.get("execution") or {}
        filled = int(_num(ex.get("filledQuantity")))
        if status == OrderStatus.SUBMITTED and filled > 0:
            status = OrderStatus.PARTIALLY_FILLED
        avg = _num(ex.get("averageFilledPrice")) or None
        fills = []
        if filled > 0 and avg:
            at = ex.get("filledAt")
            fills.append(
                FillInfo(
                    quantity=filled,
                    price=avg,
                    fee=_num(ex.get("commission")),
                    tax=_num(ex.get("tax")),
                    filled_at=datetime.fromisoformat(at) if at else self.clock.now(),
                )
            )
            if o.get("side") == "BUY":
                opened = self.kv.get("live_opened")
                if o.get("symbol") and o["symbol"] not in opened:
                    opened[o["symbol"]] = self._today()
                    self.kv.set("live_opened", opened)
        path = [status]
        if status in (OrderStatus.CANCELED, OrderStatus.REJECTED) and filled > 0:
            status = OrderStatus.CANCELED  # 일부 체결 후 끝난 주문: 체결분은 남기고 나머지는 취소로 기록
            path = [OrderStatus.PARTIALLY_FILLED, status]
        return OrderResult(
            broker_order_id=broker_order_id,
            client_order_id="",
            status=status,
            filled_qty=filled,
            avg_fill_price=avg,
            fills=fills,
            reject_reason=f"toss-{o.get('status', '').lower()}" if status == OrderStatus.REJECTED else None,
            fill_basis="LIVE",
            path=path,
            cumulative=True,
        )
