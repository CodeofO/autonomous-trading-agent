import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, get, post } from "../api/client";
import { ChatFeed } from "../components/chat/ChatFeed";
import { EquityChart } from "../components/charts";
import { useEvents } from "../events";
import { DEFAULT_PERSONA, usePortfolio, useSystem } from "../hooks";
import { fmtDateTime, fmtPct, fmtWon, signClass } from "../utils/format";
import { HoldingsTable } from "./Portfolio";

const JOB_KO: Record<string, string> = {
  morning: "☀️ 장전 판단",
  hourly: "⏰ 정시 판단",
  report: "📊 마감 리포트",
};

export function Dashboard() {
  const qc = useQueryClient();
  const { data: sys } = useSystem();
  const { data: pf } = usePortfolio();
  const { messages, typing } = useEvents();
  const equity = useQuery({
    queryKey: ["equity"],
    queryFn: () => get<{ ts: string; equity: number }[]>("/api/equity?limit=1000"),
  });
  const run = useMutation({
    mutationFn: () => post("/api/agent/run"),
    onSuccess: () => qc.invalidateQueries(),
  });
  const pause = useMutation({
    mutationFn: (p: boolean) => post(p ? "/api/engine/pause" : "/api/engine/resume"),
    onSuccess: () => qc.invalidateQueries(),
  });
  const persona = sys?.persona ?? DEFAULT_PERSONA;
  const unrealized = pf?.positions.reduce((s, p) => s + p.unrealized_pnl, 0) ?? 0;

  return (
    <div className="dashboard">
      <section className="kpis">
        <Kpi
          label="총자산"
          value={fmtWon(pf?.equity)}
          sub={`누적 ${fmtPct(pf?.total_return_pct)}`}
          tone={pf?.total_return_pct}
        />
        <Kpi
          label="평가손익"
          value={fmtWon(unrealized)}
          sub={`실현 ${fmtWon(pf?.realized_pnl)}`}
          tone={unrealized}
        />
        <Kpi label="당일손익" value={fmtPct(pf?.daily_pnl_pct)} sub="장 시작 대비" tone={pf?.daily_pnl_pct} />
        <Kpi label="현금" value={fmtWon(pf?.cash)} sub={`주문가능 ${fmtWon(pf?.available_cash)}`} />
      </section>

      <div className="dash-grid">
        <div className="dash-left">
          <section className="card">
            <div className="card-head">
              <h2>자산 추이</h2>
              <span className="muted">기준 시세일 {pf?.latest_price_day ?? "—"}</span>
            </div>
            <EquityChart points={(equity.data ?? []).map((p) => ({ t: p.ts, v: p.equity }))} />
          </section>

          <section className="card agent-panel">
            <div className="card-head">
              <h2>
                {persona.emoji} {persona.name} 매니저
              </h2>
              {sys?.agent_running ? (
                <span className="pill live">판단 중…</span>
              ) : (
                <span className="pill">대기</span>
              )}
            </div>
            <p className="muted">{sys?.order_window}</p>
            <ul className="next-runs">
              {sys?.next_runs.map((r) => (
                <li key={r.at}>
                  <span>{JOB_KO[r.name] ?? r.name}</span>
                  <time>{fmtDateTime(r.at)}</time>
                </li>
              ))}
            </ul>
            <div className="btn-row">
              <button
                className="btn primary"
                onClick={() => run.mutate()}
                disabled={run.isPending || sys?.agent_running || sys?.engine_status !== "RUNNING"}
              >
                🧠 지금 판단 실행
              </button>
              {sys?.engine_status === "RUNNING" && (
                <button className="btn ghost" onClick={() => pause.mutate(true)}>
                  ⏸ 일시정지
                </button>
              )}
              {sys?.engine_status === "PAUSED" && (
                <button className="btn ghost" onClick={() => pause.mutate(false)}>
                  ▶ 재개
                </button>
              )}
            </div>
            {run.isError && <p className="error">{errorText(run.error)}</p>}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>보유 종목</h2>
              <Link to="/portfolio">전체 보기 →</Link>
            </div>
            <HoldingsTable positions={pf?.positions ?? []} />
          </section>
        </div>

        <section className="card room-mini">
          <div className="card-head">
            <h2>💬 트레이딩 룸</h2>
            <Link to="/room">크게 보기 →</Link>
          </div>
          <ChatFeed messages={messages} typing={typing} persona={persona} showTools={false} />
        </section>
      </div>
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: number }) {
  return (
    <div className="kpi card">
      <span className="kpi-label">{label}</span>
      <b className={`kpi-value ${signClass(tone)}`}>{value}</b>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  );
}
