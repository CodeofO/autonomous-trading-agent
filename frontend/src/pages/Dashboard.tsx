import { memo, useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { ChatFeed } from "../components/chat/ChatFeed";
import { HoldingsTable } from "../components/HoldingsTable";
import { IndicatorStrip } from "../components/IndicatorStrip";
import { EquityChart } from "../components/LazyCharts";
import { SkeletonLines } from "../components/Skeleton";
import { usePersona, usePortfolio, useShowTools, useSystem } from "../hooks";
import { useChat } from "../live/hooks";
import { invalidateNames, MARKET_META, useApi, useCurrency } from "../market";
import { toast } from "../ui/toast";
import { withLivePoint } from "../utils/equity";
import { fmtDateTime, fmtPct, JOB_KO, money, MONEY_FMT, pctFmt, signClass } from "../utils/format";

/**
 * 시장 대시보드. 화면 틀은 아무것도 구독하지 않고, 폴링·실시간으로 바뀌는 부분(지표·계좌·차트·매니저·채팅)은
 * 각자 필요한 데이터만 구독한다 — 계좌가 바뀌어도 채팅 200줄이, 채팅이 와도 차트가 다시 그려지지 않는다.
 */
export function Dashboard() {
  return (
    <div className="dashboard">
      <IndicatorStrip />
      <Kpis />
      <div className="dash-grid">
        <div className="dash-left">
          <EquityCard />
          <AgentPanel />
          <HoldingsCard />
        </div>
        <DashboardChat />
      </div>
    </div>
  );
}

function Kpis() {
  const cur = useCurrency();
  const { data: pf, isPending } = usePortfolio();
  const unrealized = pf?.positions.reduce((s, p) => s + p.unrealized_pnl, 0) ?? 0;
  const fmt = MONEY_FMT[cur];
  // 받기 전에는 '—' 대신 숫자 자리 표시 (도착하면서 글자 너비가 바뀌어도 카드 크기는 그대로)
  return (
    <section className="kpis" aria-busy={isPending}>
      <Kpi
        label="총자산"
        value={pf?.equity}
        format={fmt}
        sub={`누적 ${fmtPct(pf?.total_return_pct)}`}
        tone={pf?.total_return_pct}
        loading={isPending}
      />
      <Kpi
        label="평가손익"
        value={pf ? unrealized : undefined}
        format={fmt}
        sub={`실현 ${money(pf?.realized_pnl, cur)}`}
        tone={unrealized}
        loading={isPending}
      />
      <Kpi
        label="당일손익"
        value={pf?.daily_pnl_pct}
        format={pctFmt}
        sub="장 시작 대비"
        tone={pf?.daily_pnl_pct}
        loading={isPending}
      />
      <Kpi
        label="현금"
        value={pf?.cash}
        format={fmt}
        sub={`주문가능 ${money(pf?.available_cash, cur)}`}
        loading={isPending}
      />
    </section>
  );
}

/** 자산 스냅샷을 다시 받는 간격 — 스냅샷은 10분마다 쌓이고, 그 사이는 '지금' 점이 메운다 */
const EQUITY_POLL_MS = 60_000;

function EquityCard() {
  const api = useApi();
  const cur = useCurrency();
  const { data: pf } = usePortfolio();
  const equity = useQuery({
    queryKey: api.key("equity"),
    queryFn: () => api.get<{ ts: string; equity: number }[]>("/api/equity?limit=1000"),
    refetchInterval: EQUITY_POLL_MS,
  });
  // 받은 데이터가 그대로면 같은 배열 — 차트가 다시 그리지 않는다
  const snaps = useMemo(() => (equity.data ?? []).map((p) => ({ t: p.ts, v: p.equity })), [equity.data]);
  // 끝에 '지금' 점(총자산과 같은 값·시각)을 붙여 차트 마지막 값 = 위 총자산
  const asOf = pf?.as_of;
  const total = pf?.equity;
  const points = useMemo(() => withLivePoint(snaps, { as_of: asOf, equity: total }), [snaps, asOf, total]);
  return (
    <section className="card">
      <div className="card-head">
        <h2>자산 추이</h2>
        <span className="muted">기준 시세일 {pf?.latest_price_day ?? "—"}</span>
      </div>
      <EquityChart points={points} currency={cur} />
    </section>
  );
}

function AgentPanel() {
  const qc = useQueryClient();
  const api = useApi();
  const { data: sys } = useSystem();
  const persona = usePersona(sys);
  const run = useMutation({
    mutationFn: () => api.post("/api/agent/run"),
    // 세션 시작: 매니저 상태·세션 목록·시장 목록 (나머지는 세션 이벤트가 알려 준다)
    onSuccess: () => {
      void invalidateNames(qc, ["system", "runs", "overview", "markets"], api.market);
      toast.info(
        `🧠 ${MARKET_META[api.market].name} 판단 세션을 시작했어요 — 트레이딩 룸에서 실시간으로 보여요`,
      );
    },
  });
  const pause = useMutation({
    mutationFn: (p: boolean) => api.post(p ? "/api/engine/pause" : "/api/engine/resume"),
    onSuccess: (_d, p) => {
      void invalidateNames(qc, ["system", "agent-structure", "overview", "markets"], api.market);
      toast(p ? "⏸ 자동매매를 잠시 멈췄어요" : "▶ 자동매매를 다시 켰어요");
    },
    onError: (e) => toast.error(`바꾸지 못했어요: ${errorText(e)}`),
  });
  return (
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
      {sys ? (
        <>
          <p className="muted">{sys.order_window}</p>
          <ul className="next-runs">
            {sys.next_runs.map((r) => (
              <li key={r.at}>
                <span>{JOB_KO[r.name] ?? r.name}</span>
                <time>{fmtDateTime(r.at)}</time>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <SkeletonLines lines={3} label="매니저 상태 불러오는 중" />
      )}
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
  );
}

function HoldingsCard() {
  const { data: pf, isPending } = usePortfolio();
  return (
    <section className="card">
      <div className="card-head">
        <h2>보유 종목</h2>
        <Link to="/portfolio">전체 보기 →</Link>
      </div>
      <HoldingsTable positions={pf?.positions ?? []} loading={isPending} />
    </section>
  );
}

/** 오른쪽 트레이딩 룸 미리보기 — 채팅·진행 상태만 구독 */
function DashboardChat() {
  const { data: sys } = useSystem();
  const persona = usePersona(sys);
  const { messages, typing, progress, ready } = useChat();
  const [showTools, setShowTools] = useShowTools();
  return (
    <section className="card room-mini">
      <div className="card-head">
        <h2>💬 트레이딩 룸</h2>
        <div className="btn-row">
          <label className="toggle">
            <input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} />
            리서치 과정 보기
          </label>
          <Link to="/room">크게 보기 →</Link>
        </div>
      </div>
      <ChatFeed
        messages={messages}
        typing={typing}
        progress={progress}
        persona={persona}
        showTools={showTools}
        loading={!ready}
      />
    </section>
  );
}

const Kpi = memo(function Kpi({
  label,
  value,
  format,
  sub,
  tone,
  loading = false,
}: {
  label: string;
  value: number | null | undefined;
  format: (v: number | null | undefined) => string;
  sub?: string;
  tone?: number;
  loading?: boolean;
}) {
  return (
    <div className="kpi card">
      <span className="kpi-label">{label}</span>
      {loading ? (
        <>
          <b className="kpi-value">
            <i className="skel-inline" aria-hidden />
          </b>
          {sub && (
            <span className="kpi-sub">
              <i className="skel-inline short" aria-hidden />
            </span>
          )}
        </>
      ) : (
        <>
          <b className={`kpi-value ${signClass(tone)}`}>
            <AnimatedNumber value={value} format={format} />
          </b>
          {sub && <span className="kpi-sub">{sub}</span>}
        </>
      )}
    </div>
  );
});
