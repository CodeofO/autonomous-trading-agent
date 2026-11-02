import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api/client";
import type { Decision, FillRow, Order, Position } from "../api/types";
import {
  ACTION_KO,
  BASIS_KO,
  fmtDateTime,
  fmtNum,
  fmtPct,
  fmtWon,
  signClass,
  STATUS_KO,
} from "../utils/format";

export function HoldingsTable({ positions }: { positions: Position[] }) {
  if (positions.length === 0)
    return <p className="muted empty">보유 종목이 없어요. 매니저가 기회를 찾는 중이에요 👀</p>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>종목</th>
          <th className="r">수량</th>
          <th className="r">평단</th>
          <th className="r">현재가</th>
          <th className="r">평가금액</th>
          <th className="r">수익률</th>
          <th className="r">비중</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol}>
            <td>
              <Link to={`/stock/${p.symbol}`}>{p.name || p.symbol}</Link>{" "}
              <small className="muted">{p.symbol}</small>
            </td>
            <td className="r">{fmtNum(p.quantity)}</td>
            <td className="r">{fmtNum(p.avg_price)}</td>
            <td className="r">{fmtNum(p.last_price)}</td>
            <td className="r">{fmtWon(p.market_value)}</td>
            <td className={`r ${signClass(p.pnl_pct)}`}>{fmtPct(p.pnl_pct)}</td>
            <td className="r">{p.weight_pct.toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Portfolio() {
  const qc = useQueryClient();
  const pf = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => get<{ positions: Position[] }>("/api/portfolio"),
  });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => get<Order[]>("/api/orders?limit=300") });
  const fills = useQuery({ queryKey: ["fills"], queryFn: () => get<FillRow[]>("/api/fills?limit=200") });
  const decisions = useQuery({
    queryKey: ["decisions"],
    queryFn: () => get<Decision[]>("/api/agent/decisions?limit=100"),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => post(`/api/orders/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries(),
  });
  const open = (orders.data ?? []).filter((o) =>
    ["PENDING", "SUBMITTED", "PARTIALLY_FILLED"].includes(o.status),
  );
  const closed = (orders.data ?? []).filter((o) => !open.includes(o));

  return (
    <div className="stack">
      <section className="card">
        <h2>보유 종목</h2>
        <HoldingsTable positions={pf.data?.positions ?? []} />
      </section>

      <section className="card">
        <h2>미체결 주문 ({open.length})</h2>
        <p className="muted">
          토스 실시세 모드는 현재가로 즉시(지정가는 도달 시) 체결되고 15:20 미체결분은 취소돼요. 가상 시장
          모드는 다음 공식 일봉으로 확정돼요.
        </p>
        <OrderTable orders={open} onCancel={(id) => cancel.mutate(id)} />
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
            <tbody>
              {fills.data?.map((f) => (
                <tr key={f.id}>
                  <td>{fmtDateTime(f.filled_at)}</td>
                  <td>
                    <Link to={`/stock/${f.symbol}`}>{f.name || f.symbol}</Link>
                  </td>
                  <td className={f.side === "BUY" ? "up" : "down"}>{ACTION_KO[f.side]}</td>
                  <td className="r">{fmtNum(f.quantity)}</td>
                  <td className="r">{fmtNum(f.price, 2)}</td>
                  <td className="r">{fmtWon(f.fee + f.tax)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>지난 주문</h2>
        <OrderTable orders={closed.slice(0, 100)} />
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
          <tbody>
            {decisions.data?.map((d) => (
              <tr key={d.id}>
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
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function OrderTable({ orders, onCancel }: { orders: Order[]; onCancel?: (id: number) => void }) {
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
          <tr key={o.id}>
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
              {o.filled_qty > 0 ? `${fmtNum(o.filled_qty)} @ ${fmtNum(o.avg_fill_price, 0)}` : "—"}
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
        ))}
      </tbody>
    </table>
  );
}
