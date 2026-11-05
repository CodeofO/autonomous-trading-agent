import { memo, useMemo, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Currency, Decision, FillRow, MarketCode, Order, Overview } from "../api/types";
import { HoldingsTable } from "../components/HoldingsTable";
import { SkeletonCards } from "../components/Skeleton";
import { usePortfolio } from "../hooks";
import { errorText, get, marketApi } from "../api/client";
import {
  currencyOf,
  invalidateNames,
  MARKET_META,
  MarketContext,
  useApi,
  useCurrency,
  useMarket,
} from "../market";
import { toast } from "../ui/toast";
import { ACTION_KO, BASIS_KO, fmtDateTime, fmtNum, fmtWon, money, price, STATUS_KO } from "../utils/format";
import { OVERVIEW_KEY } from "../utils/overview";

const OPEN_STATUSES = new Set(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]);

/** 포트폴리오·주문. 위 전환이 '전체'면 국장·미장을 한 화면에 (D-61), 아니면 그 시장만 */
export function Portfolio() {
  const { view, available } = useMarket();
  if (view === "ALL" && available.length > 1) return <PortfolioAll markets={available} />;
  return <MarketPortfolio />;
}

function MarketPortfolio() {
  const qc = useQueryClient();
  const api = useApi();
  const cur = useCurrency();
  const pf = usePortfolio();
  const orders = useQuery({
    queryKey: api.key("orders"),
    queryFn: () => api.get<Order[]>("/api/orders?limit=300"),
  });
  const fills = useQuery({
    queryKey: api.key("fills"),
    queryFn: () => api.get<FillRow[]>("/api/fills?limit=200"),
  });
  const decisions = useQuery({
    queryKey: api.key("decisions"),
    queryFn: () => api.get<Decision[]>("/api/agent/decisions?limit=100"),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => api.post(`/api/orders/${id}/cancel`),
    // 취소한 주문·주문 가능 현금·한눈에 보기만 다시 (나머지는 이벤트가 알려 준다)
    onSuccess: (_d, id) => {
      void invalidateNames(qc, ["orders", "portfolio", "decisions", "overview"], api.market);
      toast(`주문 #${id} 을(를) 취소했어요`);
    },
    // 표의 줄마다 오류를 보여 줄 자리가 없다 — 토스트로
    onError: (e) => toast.error(`주문을 취소하지 못했어요: ${errorText(e)}`),
  });
  const { mutate: cancelOrder } = cancel; // 늘 같은 함수 — 주문 줄(memo)이 다시 그려지지 않게
  // 미체결/지난 주문을 한 번에 나눈다
  const [open, closed] = useMemo(() => {
    const o: Order[] = [];
    const c: Order[] = [];
    for (const x of orders.data ?? []) (OPEN_STATUSES.has(x.status) ? o : c).push(x);
    return [o, c.slice(0, 100)];
  }, [orders.data]);

  // 표 다섯 개가 따로 도착하며 아래 표를 밀어내지 않게, 처음에는 모두 받을 때까지 자리 표시 한 번만
  if (pf.isPending || orders.isPending || fills.isPending || decisions.isPending)
    return <SkeletonCards heights={[180, 140, 220, 220, 260]} label="포트폴리오 불러오는 중" />;

  return (
    <div className="stack">
      <section className="card">
        <h2>보유 종목</h2>
        <HoldingsTable positions={pf.data?.positions ?? []} />
      </section>

      <section className="card">
        <h2>미체결 주문 ({open.length})</h2>
        <p className="muted">
          {cur === "USD"
            ? "미장은 달러 모의계좌예요. 정규장(한국 시각 밤) 현재가로 즉시(지정가는 도달 시) 체결되고, 장 마감 전 미체결분은 취소돼요."
            : "토스 실시세 모드는 현재가로 즉시(지정가는 도달 시) 체결되고 15:20 미체결분은 취소돼요. 가상 시장 모드는 다음 공식 일봉으로 확정돼요."}
        </p>
        <OrderTable orders={open} currency={cur} onCancel={cancelOrder} />
      </section>

      <section className="card">
        <h2>체결 내역</h2>
        {(fills.data ?? []).length === 0 ? (
          <p className="muted empty">아직 체결이 없어요.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>체결일</th>
                <th>종목</th>
                <th>구분</th>
                <th className="r">수량</th>
                <th className="r">가격</th>
                <th className="r">수수료+세금</th>
              </tr>
            </thead>
            <tbody>{fills.data?.map((f) => <FillTr key={f.id} f={f} currency={cur} />)}</tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>지난 주문</h2>
        <OrderTable orders={closed} currency={cur} />
      </section>

      <section className="card">
        <h2>에이전트 결정 기록</h2>
        <table className="table">
          <thead>
            <tr>
              <th>시각</th>
              <th>종목</th>
              <th>결정</th>
              <th>확신도</th>
              <th>이유</th>
              <th>결과</th>
            </tr>
          </thead>
          <tbody>{decisions.data?.map((d) => <DecisionTr key={d.id} d={d} />)}</tbody>
        </table>
      </section>
    </div>
  );
}

// 표의 줄은 memo — 다시 받아도 바뀌지 않은 줄(같은 객체, React Query 가 유지)은 다시 그리지 않는다
const FillTr = memo(function FillTr({
  f,
  currency,
  market,
}: {
  f: FillRow;
  currency: Currency;
  market?: MarketCode;
}) {
  return (
    <tr>
      <td>{fmtDateTime(f.filled_at)}</td>
      {market && <MarketCell m={market} />}
      <td>
        <StockLink symbol={f.symbol} name={f.name} market={market} />
      </td>
      <td className={f.side === "BUY" ? "up" : "down"}>{ACTION_KO[f.side]}</td>
      <td className="r">{fmtNum(f.quantity)}</td>
      <td className="r">{price(f.price, currency, 2)}</td>
      <td className="r">{money(f.fee + f.tax, currency)}</td>
    </tr>
  );
});

const DecisionTr = memo(function DecisionTr({ d, market }: { d: Decision; market?: MarketCode }) {
  return (
    <tr>
      <td>{fmtDateTime(d.created_at)}</td>
      {market && <MarketCell m={market} />}
      <td>
        <StockLink symbol={d.symbol} name={d.name} market={market} />
      </td>
      <td>
        <span className={`action-badge ${d.action.toLowerCase()}`}>{ACTION_KO[d.action]}</span>
      </td>
      <td>{Math.round(d.confidence * 100)}%</td>
      <td className="wrap">{d.rationale}</td>
      <td className="wrap muted">{d.outcome}</td>
    </tr>
  );
});

const OrderTr = memo(function OrderTr({
  o,
  currency,
  onCancel,
  market,
}: {
  o: Order;
  currency: Currency;
  onCancel?: (id: number, market?: MarketCode) => void;
  market?: MarketCode;
}) {
  return (
    <tr>
      <td>{fmtDateTime(o.created_at)}</td>
      {market && <MarketCell m={market} />}
      <td>
        <StockLink symbol={o.symbol} name={o.name} market={market} />
      </td>
      <td className={o.side === "BUY" ? "up" : "down"}>{ACTION_KO[o.side]}</td>
      <td className="r">{fmtNum(o.quantity)}</td>
      <td>
        {o.trade_day} {BASIS_KO[o.fill_basis] ?? ""}
      </td>
      <td>
        <span className={`pill ${o.status.toLowerCase()}`}>{STATUS_KO[o.status] ?? o.status}</span>
      </td>
      <td className="r">
        {o.filled_qty > 0 ? `${fmtNum(o.filled_qty)} @ ${price(o.avg_fill_price, currency)}` : "—"}
      </td>
      <td className="wrap muted">{o.reject_reason ?? o.reason}</td>
      {onCancel && (
        <td>
          <button className="btn tiny" onClick={() => onCancel(o.id, market)}>
            취소
          </button>
        </td>
      )}
    </tr>
  );
});

function OrderTable({
  orders,
  currency,
  onCancel,
}: {
  orders: Order[];
  currency: Currency;
  onCancel?: (id: number) => void;
}) {
  if (orders.length === 0) return <p className="muted empty">없음</p>;
  return (
    <table className="table">
      <OrderHead cancel={!!onCancel} />
      <tbody>
        {orders.map((o) => (
          <OrderTr key={o.id} o={o} currency={currency} onCancel={onCancel} />
        ))}
      </tbody>
    </table>
  );
}

function OrderHead({ cancel, withMarket = false }: { cancel: boolean; withMarket?: boolean }) {
  return (
    <thead>
      <tr>
        <th>접수</th>
        {withMarket && <th>시장</th>}
        <th>종목</th>
        <th>구분</th>
        <th className="r">수량</th>
        <th>체결기준</th>
        <th>상태</th>
        <th className="r">체결</th>
        <th>사유</th>
        {cancel && <th />}
      </tr>
    </thead>
  );
}

// ====================================================================== 전체 (국장 + 미장, D-61)

/** 시장 표시 칸 (전체 보기의 합친 표) */
function MarketCell({ m }: { m: MarketCode }) {
  const meta = MARKET_META[m];
  return (
    <td className="pf-mkt" title={meta.name}>
      {meta.flag}
    </td>
  );
}

/** 종목 링크 — 전체 보기에서는 '전체'를 그대로 두고 그 종목의 시장 화면을 연다 */
function StockLink({ symbol, name, market }: { symbol: string; name?: string; market?: MarketCode }) {
  const { focusMarket } = useMarket();
  return (
    <Link to={`/stock/${symbol}`} onClick={market ? () => focusMarket(market) : undefined}>
      {name || symbol}
    </Link>
  );
}

/** 이 안에서는 지정한 시장으로 동작한다 (보유 종목 표·통화·API 가 그 시장 것) */
function MarketScope({ m, children }: { m: MarketCode; children: ReactNode }) {
  const state = useMarket();
  const value = useMemo(() => ({ ...state, market: m, last: m }), [state, m]);
  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>;
}

type Tagged<T> = T & { _m: MarketCode };

const byTime =
  <T,>(key: (x: T) => string | null | undefined) =>
  (a: T, b: T) =>
    String(key(b) ?? "").localeCompare(String(key(a) ?? ""));

/** 시장별 쿼리 하나 (시장 목록이 정해져 있어 훅 순서가 바뀌지 않는다) */
function useMarketList<T>(m: MarketCode, name: string, path: string, enabled: boolean) {
  const api = marketApi(m);
  return useQuery({ queryKey: api.key(name), queryFn: () => api.get<T[]>(path), enabled });
}

function PortfolioAll({ markets }: { markets: MarketCode[] }) {
  const qc = useQueryClient();
  const hasUS = markets.includes("US");
  const krPf = usePortfolio("KR");
  const usPf = usePortfolio("US");
  const krOrders = useMarketList<Order>("KR", "orders", "/api/orders?limit=300", true);
  const usOrders = useMarketList<Order>("US", "orders", "/api/orders?limit=300", hasUS);
  const krFills = useMarketList<FillRow>("KR", "fills", "/api/fills?limit=200", true);
  const usFills = useMarketList<FillRow>("US", "fills", "/api/fills?limit=200", hasUS);
  const krDec = useMarketList<Decision>("KR", "decisions", "/api/agent/decisions?limit=100", true);
  const usDec = useMarketList<Decision>("US", "decisions", "/api/agent/decisions?limit=100", hasUS);
  const overview = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => get<Overview>("/api/overview"),
    refetchInterval: 15_000,
  });
  const cancel = useMutation({
    mutationFn: ({ id, m }: { id: number; m: MarketCode }) => marketApi(m).post(`/api/orders/${id}/cancel`),
    onSuccess: (_d, { id, m }) => {
      void invalidateNames(qc, ["orders", "portfolio", "decisions", "overview"], m);
      toast(`${MARKET_META[m].flag} 주문 #${id} 을(를) 취소했어요`);
    },
    onError: (e) => toast.error(`주문을 취소하지 못했어요: ${errorText(e)}`),
  });
  const { mutate } = cancel;
  const onCancel = useMemo(() => (id: number, m?: MarketCode) => mutate({ id, m: m ?? "KR" }), [mutate]);

  const tag = <T,>(rows: T[] | undefined, m: MarketCode): Tagged<T>[] =>
    (rows ?? []).map((x) => ({ ...x, _m: m }));
  const [open, closed] = useMemo(() => {
    const all = [...tag(krOrders.data, "KR"), ...tag(usOrders.data, "US")].sort(
      byTime((o: Tagged<Order>) => o.created_at),
    );
    return [
      all.filter((o) => OPEN_STATUSES.has(o.status)),
      all.filter((o) => !OPEN_STATUSES.has(o.status)).slice(0, 100),
    ];
  }, [krOrders.data, usOrders.data]);
  const fills = useMemo(
    () =>
      [...tag(krFills.data, "KR"), ...tag(usFills.data, "US")]
        .sort(byTime((f: Tagged<FillRow>) => f.filled_at))
        .slice(0, 200),
    [krFills.data, usFills.data],
  );
  const decisions = useMemo(
    () =>
      [...tag(krDec.data, "KR"), ...tag(usDec.data, "US")]
        .sort(byTime((d: Tagged<Decision>) => d.created_at))
        .slice(0, 100),
    [krDec.data, usDec.data],
  );

  if (krPf.isPending || krOrders.isPending || krFills.isPending)
    return <SkeletonCards heights={[120, 180, 180, 140, 220]} label="포트폴리오 불러오는 중" />;

  const ov = overview.data;
  const pfOf = (m: MarketCode) => (m === "KR" ? krPf : usPf);
  return (
    <div className="stack" data-testid="portfolio-all">
      <section className="card pf-all-total" aria-label="전체 자산">
        <div className="pf-all-head">
          <h2>🧭 전체 자산</h2>
          <b className="pf-all-sum num">
            {ov?.total_krw.equity != null ? fmtWon(Math.round(ov.total_krw.equity)) : "—"}
          </b>
        </div>
        <ul className="pf-all-markets">
          {markets.map((m) => {
            const pf = pfOf(m).data;
            return (
              <li key={m}>
                <span>
                  {MARKET_META[m].flag} {MARKET_META[m].name}
                </span>
                <b className="num">{money(pf?.equity, currencyOf(m))}</b>
                <small className="muted">
                  현금 {money(pf?.cash, currencyOf(m))} · {pf?.positions.length ?? 0}종목
                </small>
              </li>
            );
          })}
        </ul>
        <p className="muted pf-all-note">
          합계는 토스 원/달러 환율
          {ov?.fx.usdkrw ? ` ${fmtNum(ov.fx.usdkrw, 2)}원` : ""}로 미장을 원화로 바꿔 더했어요. 위 전환에서
          국장·미장을 고르면 그 시장만 봐요.
        </p>
      </section>

      {markets.map((m) => (
        <MarketScope key={m} m={m}>
          <section className="card">
            <h2>
              {MARKET_META[m].flag} {MARKET_META[m].name} 보유 종목
            </h2>
            <HoldingsTable positions={pfOf(m).data?.positions ?? []} loading={pfOf(m).isPending} />
          </section>
        </MarketScope>
      ))}

      <section className="card">
        <h2>미체결 주문 ({open.length})</h2>
        <MergedOrders orders={open} onCancel={onCancel} />
      </section>

      <section className="card">
        <h2>체결 내역</h2>
        {fills.length === 0 ? (
          <p className="muted empty">아직 체결이 없어요.</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>체결일</th>
                <th>시장</th>
                <th>종목</th>
                <th>구분</th>
                <th className="r">수량</th>
                <th className="r">가격</th>
                <th className="r">수수료+세금</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <FillTr key={`${f._m}:${f.id}`} f={f} currency={currencyOf(f._m)} market={f._m} />
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>지난 주문</h2>
        <MergedOrders orders={closed} />
      </section>

      <section className="card">
        <h2>에이전트 결정 기록</h2>
        <table className="table">
          <thead>
            <tr>
              <th>시각</th>
              <th>시장</th>
              <th>종목</th>
              <th>결정</th>
              <th>확신도</th>
              <th>이유</th>
              <th>결과</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d) => (
              <DecisionTr key={`${d._m}:${d.id}`} d={d} market={d._m} />
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function MergedOrders({
  orders,
  onCancel,
}: {
  orders: Tagged<Order>[];
  onCancel?: (id: number, m?: MarketCode) => void;
}) {
  if (orders.length === 0) return <p className="muted empty">없음</p>;
  return (
    <table className="table">
      <OrderHead cancel={!!onCancel} withMarket />
      <tbody>
        {orders.map((o) => (
          <OrderTr
            key={`${o._m}:${o.id}`}
            o={o}
            currency={currencyOf(o._m)}
            onCancel={onCancel}
            market={o._m}
          />
        ))}
      </tbody>
    </table>
  );
}
