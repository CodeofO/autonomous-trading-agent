import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { errorText, get, post } from "../api/client";
import type { BacktestResult, Strategy } from "../api/types";
import { EquityChart } from "../components/charts";
import { ACTION_KO, fmtNum, fmtPct, fmtWon, signClass } from "../utils/format";

export function Backtest() {
  const strategies = useQuery({
    queryKey: ["strategies"],
    queryFn: () => get<Strategy[]>("/api/strategies"),
  });
  const universe = useQuery({
    queryKey: ["universe"],
    queryFn: () => get<{ symbol: string; name: string }[]>("/api/market/universe?limit=40"),
  });
  const [type, setType] = useState("ma_cross");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [start, setStart] = useState("2026-01-02");
  const [end, setEnd] = useState("2026-10-30");
  const [size, setSize] = useState(10);
  const strat = strategies.data?.find((s) => s.type === type);
  const run = useMutation({
    mutationFn: () =>
      post<BacktestResult>("/api/backtest", {
        strategy_type: type,
        params: strat?.params ?? {},
        symbols,
        start,
        end,
        position_size: size / 100,
      }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (symbols.length) run.mutate();
  };
  const r = run.data;

  return (
    <div className="stack">
      <section className="card">
        <h2>🧪 백테스트</h2>
        <p className="muted">
          실거래 경로와 같은 전략·리스크·모의 체결 코드를 씁니다. 매일 14:00에 전일까지 공개된 일봉으로
          판단하고 당일 종가로 체결해요(미래 정보 차단). LLM 에이전트는 미래를 알 수 있어 백테스트하지 않아요.
        </p>
        <form className="form grid-3" onSubmit={submit}>
          <label>
            전략
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {strategies.data?.map((s) => (
                <option key={s.type} value={s.type}>
                  {s.label} (
                  {Object.entries(s.params)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ")}
                  )
                </option>
              ))}
            </select>
          </label>
          <label>
            시작일
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>
            종료일
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label>
            1회 매수 비중(%)
            <input
              type="number"
              min={1}
              max={100}
              value={size}
              onChange={(e) => setSize(Number(e.target.value))}
            />
          </label>
          <div className="full">
            <span className="muted">종목 선택 ({symbols.length}/20)</span>
            <div className="symbol-picker">
              {universe.data?.map((u) => (
                <button
                  type="button"
                  key={u.symbol}
                  className={`chip-btn ${symbols.includes(u.symbol) ? "on" : ""}`}
                  onClick={() =>
                    setSymbols((cur) =>
                      cur.includes(u.symbol)
                        ? cur.filter((x) => x !== u.symbol)
                        : [...cur, u.symbol].slice(0, 20),
                    )
                  }
                >
                  {u.name || u.symbol}
                </button>
              ))}
            </div>
          </div>
          <div className="btn-row full">
            <button className="btn primary" disabled={!symbols.length || run.isPending}>
              {run.isPending ? "계산 중…" : "실행"}
            </button>
            {run.isError && <span className="error">{errorText(run.error)}</span>}
          </div>
        </form>
      </section>

      {r && (
        <>
          <section className="kpis">
            <Metric label="수익률" value={fmtPct(r.total_return_pct)} tone={r.total_return_pct} />
            <Metric label="최대낙폭(MDD)" value={fmtPct(r.mdd_pct)} tone={r.mdd_pct} />
            <Metric label="승률" value={r.win_rate_pct == null ? "—" : `${r.win_rate_pct.toFixed(1)}%`} />
            <Metric label="거래 수" value={`${r.trades}건 (왕복 ${r.round_trips})`} />
          </section>
          <section className="card">
            <div className="card-head">
              <h2>자산 곡선</h2>
              <span className="muted">
                {fmtWon(r.initial_cash)} → {fmtWon(r.final_equity)} · 리스크 차단 {r.risk_blocks}건
              </span>
            </div>
            <EquityChart points={r.equity_curve.map((p) => ({ t: p.day, v: p.equity }))} height={280} />
          </section>
          <section className="card">
            <h2>거래 내역</h2>
            <table className="table">
              <thead>
                <tr>
                  <th>일자</th>
                  <th>종목</th>
                  <th>구분</th>
                  <th className="r">수량</th>
                  <th className="r">가격</th>
                </tr>
              </thead>
              <tbody>
                {r.trades_list.map((t, i) => (
                  <tr key={i}>
                    <td>{t.day}</td>
                    <td>{t.symbol}</td>
                    <td className={t.side === "BUY" ? "up" : "down"}>{ACTION_KO[t.side]}</td>
                    <td className="r">{fmtNum(t.quantity)}</td>
                    <td className="r">{fmtNum(t.price, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div className="kpi card">
      <span className="kpi-label">{label}</span>
      <b className={`kpi-value ${signClass(tone)}`}>{value}</b>
    </div>
  );
}
