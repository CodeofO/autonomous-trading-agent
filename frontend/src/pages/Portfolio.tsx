import { memo, useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Currency, Decision, FillRow, Order } from "../api/types";
import { HoldingsTable } from "../components/HoldingsTable";
import { SkeletonCards } from "../components/Skeleton";
import { usePortfolio } from "../hooks";
import { errorText } from "../api/client";
import { invalidateNames, useApi, useCurrency } from "../market";
import { toast } from "../ui/toast";
import { ACTION_KO, BASIS_KO, fmtDateTime, fmtNum, money, price, STATUS_KO } from "../utils/format";

const OPEN_STATUSES = new Set(["PENDING", "SUBMITTED", "PARTIALLY_FILLED"]);

export function Portfolio() {
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
const FillTr = memo(function FillTr({ f, currency }: { f: FillRow; currency: Currency }) {
  return (
    <tr>
      <td>{fmtDateTime(f.filled_at)}</td>
      <td>
        <Link to={`/stock/${f.symbol}`}>{f.name || f.symbol}</Link>
      </td>
      <td className={f.side === "BUY" ? "up" : "down"}>{ACTION_KO[f.side]}</td>
      <td className="r">{fmtNum(f.quantity)}</td>
      <td className="r">{price(f.price, currency, 2)}</td>
      <td className="r">{money(f.fee + f.tax, currency)}</td>
    </tr>
  );
});

const DecisionTr = memo(function DecisionTr({ d }: { d: Decision }) {
  return (
    <tr>
      <td>{fmtDateTime(d.created_at)}</td>
      <td>
        <Link to={`/stock/${d.symbol}`}>{d.name || d.symbol}</Link>
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
}: {
  o: Order;
  currency: Currency;
  onCancel?: (id: number) => void;
}) {
  return (
    <tr>
      <td>{fmtDateTime(o.created_at)}</td>
      <td>
        <Link to={`/stock/${o.symbol}`}>{o.name || o.symbol}</Link>
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
          <button className="btn tiny" onClick={() => onCancel(o.id)}>
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
      <thead>
        <tr>
          <th>접수</th>
          <th>종목</th>
          <th>구분</th>
          <th className="r">수량</th>
          <th>체결기준</th>
          <th>상태</th>
          <th className="r">체결</th>
          <th>사유</th>
          {onCancel && <th />}
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <OrderTr key={o.id} o={o} currency={currency} onCancel={onCancel} />
        ))}
      </tbody>
    </table>
  );
}
