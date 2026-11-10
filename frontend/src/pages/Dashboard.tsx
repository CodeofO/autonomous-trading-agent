import { memo, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { ChatFeed } from "../components/chat/ChatFeed";
import { HoldingsTable } from "../components/HoldingsTable";
import { IndicatorStrip } from "../components/IndicatorStrip";
import { EquityChart } from "../components/LazyCharts";
import { failedOf, LoadError } from "../components/LoadError";
import { SkeletonLines } from "../components/Skeleton";
import { usePersona, usePortfolio, useShowTools, useSystem } from "../hooks";
import { useChat } from "../live/hooks";
import { invalidateNames, MARKET_META, useApi, useCurrency } from "../market";
import { toast } from "../ui/toast";
import { withLivePoint } from "../utils/equity";
import type { Decision, FillRow, Performance } from "../api/types";
import {
  ACTION_KO,
  fmtDateTime,
  fmtHm,
  fmtPct,
  JOB_KO,
  money,
  MONEY_FMT,
  pctFmt,
  price,
  signClass,
  STATUS_KO,
} from "../utils/format";

/**
 * 시장 대시보드. 화면 틀은 아무것도 구독하지 않고, 폴링·실시간으로 바뀌는 부분(지표·계좌·차트·매니저·채팅)은
 * 각자 필요한 데이터만 구독한다 — 계좌가 바뀌어도 채팅 200줄이, 채팅이 와도 차트가 다시 그려지지 않는다.
 */
export function Dashboard() {
  return (
    <div className="dashboard">
      <AccountHero />
      <IndicatorStrip />
      <div className="dash-grid">
        <div className="dash-left">
          <EquityCard />
          <TodayCard />
          <HoldingsCard />
        </div>
        <DashboardChat />
      </div>
    </div>
  );
}

/**
 * 계좌 한 줄 요약 + 매니저 상태. 총자산은 중립색(오르내림 색은 손익에만),
 * 오른쪽에 매니저가 지금 무엇을 하는지·다음 판단·바로 쓸 조작을 둔다.
 */
function AccountHero() {
  const cur = useCurrency();
  const pfq = usePortfolio();
  const { data: pf, isPending } = pfq;
  const unrealized = pf?.positions.reduce((s, p) => s + p.unrealized_pnl, 0) ?? 0;
  const fmt = MONEY_FMT[cur];
  const cashPct = pf && pf.equity > 0 ? (pf.cash / pf.equity) * 100 : undefined;
  if (failedOf([pfq]).length) return <LoadError what="계좌를" queries={[pfq]} />;
  return (
    <section className="card hero" aria-busy={isPending}>
      <div className="hero-account">
        <span className="kpi-label">총자산</span>
        <b className="hero-total">
          {isPending ? (
            <i className="skel-inline" aria-hidden />
          ) : (
            <AnimatedNumber value={pf?.equity} format={fmt} />
          )}
        </b>
        <dl className="hero-stats">
          <Stat
            label="당일손익"
            loading={isPending}
            tone={pf?.daily_pnl_pct}
            value={pf?.daily_pnl_pct}
            format={pctFmt}
          />
          <Stat
            label="누적 수익률"
            loading={isPending}
            tone={pf?.total_return_pct}
            value={pf?.total_return_pct}
            format={pctFmt}
          />
          <Stat
            label="평가손익"
            loading={isPending}
            tone={unrealized}
            value={pf ? unrealized : undefined}
            format={fmt}
            sub={`실현 ${money(pf?.realized_pnl, cur)}`}
          />
          <Stat
            label="현금"
            loading={isPending}
            value={pf?.cash}
            format={fmt}
            sub={
              cashPct == null
                ? undefined
                : `비중 ${cashPct.toFixed(1)}% · 주문가능 ${money(pf?.available_cash, cur)}`
            }
          />
        </dl>
      </div>
      <AgentPanel />
    </section>
  );
}

const Stat = memo(function Stat({
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
    <div className="hero-stat">
      <dt>{label}</dt>
      <dd>
        {loading ? (
          <i className="skel-inline short" aria-hidden />
        ) : (
          <b className={signClass(tone)}>
            <AnimatedNumber value={value} format={format} />
          </b>
        )}
        {sub && !loading && <small>{sub}</small>}
      </dd>
    </div>
  );
});

/** 자산 스냅샷을 다시 받는 간격 — 스냅샷은 10분마다 쌓이고, 그 사이는 '지금' 점이 메운다 */
const EQUITY_POLL_MS = 60_000;

function EquityCard() {
  const api = useApi();
  const cur = useCurrency();
  const { data: pf } = usePortfolio();
  const [tab, setTab] = useState<"equity" | "bench">("equity");
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
        <h2>{tab === "equity" ? "자산 추이" : "지수 대비 성과"}</h2>
        <div className="seg" role="tablist" aria-label="차트 종류">
          <button
            role="tab"
            className={tab === "equity" ? "active" : ""}
            aria-selected={tab === "equity"}
            onClick={() => setTab("equity")}
          >
            자산 추이
          </button>
          <button
            role="tab"
            className={tab === "bench" ? "active" : ""}
            aria-selected={tab === "bench"}
            onClick={() => setTab("bench")}
          >
            지수 대비
          </button>
        </div>
      </div>
      {tab === "equity" ? (
        <>
          <EquityChart points={points} currency={cur} />
          <p className="muted chart-foot">기준 시세일 {pf?.latest_price_day ?? "—"}</p>
        </>
      ) : (
        <BenchmarkPanel />
      )}
    </section>
  );
}

const BENCH_COLORS = ["var(--mkt-us)", "var(--warn)", "var(--ok)"];

/** 일 단위 누적 수익률: 이 계좌 vs 지수 (첫날 = 0%). 기록이 이틀 이상 쌓여야 선이 된다. */
function BenchmarkPanel() {
  const api = useApi();
  const q = useQuery({
    queryKey: api.key("performance"),
    queryFn: () => api.get<Performance>("/api/performance?days=180"),
    staleTime: 300_000,
  });
  if (q.isError) return <LoadError what="성과 비교를" queries={[q]} />;
  if (q.isPending) return <SkeletonLines lines={6} label="성과 비교 불러오는 중" />;
  const p = q.data;
  if (p.days.length < 2)
    return <p className="muted today-empty">기록이 이틀 이상 쌓이면 지수와 나란히 보여 드려요.</p>;
  const series = [
    { key: "ME", name: "내 계좌", pct: p.account_pct, color: "var(--accent)" },
    ...p.benchmarks.map((b, i) => ({ ...b, color: BENCH_COLORS[i % BENCH_COLORS.length] })),
  ];
  const all = series.flatMap((x) => x.pct);
  const lo = Math.min(0, ...all);
  const hi = Math.max(0, ...all);
  const W = 600;
  const H = 200;
  const x = (i: number) => (i / (p.days.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo || 1)) * H;
  const last = p.days.length - 1;
  const me = p.account_pct[last];
  return (
    <div className="bench">
      <ul className="bench-legend">
        {series.map((sr) => (
          <li key={sr.key}>
            <i style={{ background: sr.color }} aria-hidden />
            {sr.name} <b className={signClass(sr.pct[last])}>{fmtPct(sr.pct[last])}</b>
            {sr.key !== "ME" && <small className="muted"> (차이 {fmtPct(me - sr.pct[last])}p)</small>}
          </li>
        ))}
      </ul>
      <svg
        className="bench-chart"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${p.days[0]}부터 내 계좌와 지수의 누적 수익률`}
      >
        <line x1="0" x2={W} y1={y(0)} y2={y(0)} className="bench-zero" />
        {series
          .slice()
          .reverse()
          .map((sr) => (
            <polyline
              key={sr.key}
              points={sr.pct.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
              fill="none"
              stroke={sr.color}
              strokeWidth={sr.key === "ME" ? 2.5 : 1.5}
              vectorEffect="non-scaling-stroke"
            />
          ))}
      </svg>
      <p className="muted chart-foot">
        {p.days[0]} ~ {p.days[last]} · 하루 마감 기준 · 첫날을 0%로 맞춘 누적 수익률
      </p>
    </div>
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
        `${MARKET_META[api.market].name} 판단 세션을 시작했어요 — 트레이딩 룸에서 실시간으로 보여요`,
      );
    },
  });
  const pause = useMutation({
    mutationFn: (p: boolean) => api.post(p ? "/api/engine/pause" : "/api/engine/resume"),
    onSuccess: (_d, p) => {
      void invalidateNames(qc, ["system", "agent-structure", "overview", "markets"], api.market);
      toast(p ? "자동매매를 잠시 멈췄어요" : "자동매매를 다시 켰어요");
    },
    onError: (e) => toast.error(`바꾸지 못했어요: ${errorText(e)}`),
  });
  return (
    <div className="hero-agent agent-panel">
      <div className="card-head">
        <h2>{persona.name} 매니저</h2>
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
            {sys.next_runs.slice(0, 2).map((r, i) => (
              <li key={r.at}>
                <span>
                  {i === 0 ? "다음 " : ""}
                  {JOB_KO[r.name] ?? r.name}
                </span>
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
          지금 판단 실행
        </button>
        {sys?.engine_status === "RUNNING" && (
          <button className="btn ghost" onClick={() => pause.mutate(true)}>
            일시정지
          </button>
        )}
        {sys?.engine_status === "PAUSED" && (
          <button className="btn ghost" onClick={() => pause.mutate(false)}>
            재개
          </button>
        )}
      </div>
      {run.isError && <p className="error">{errorText(run.error)}</p>}
    </div>
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
        <h2>트레이딩 룸</h2>
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

/** 결정 결과 문구의 주문 상태 코드를 한글로 ("주문 FILLED (5주)" → "주문 체결 (5주)") */
export const outcomeText = (o: string) =>
  o.replace(/주문 ([A-Z_]+)/, (_m, st: string) => `주문 ${STATUS_KO[st] ?? st}`);

const sameDay = (iso: string, now: number) => new Date(iso).toDateString() === new Date(now).toDateString();

/** 오늘의 활동: 오늘 매니저가 내린 매수·매도 결정과 체결을 시간순으로. 유지는 건수만. */
function TodayCard() {
  const api = useApi();
  const cur = useCurrency();
  const decisions = useQuery({
    queryKey: api.key("decisions"),
    queryFn: () => api.get<Decision[]>("/api/agent/decisions?limit=100"),
  });
  const fills = useQuery({
    queryKey: api.key("fills"),
    queryFn: () => api.get<FillRow[]>("/api/fills?limit=200"),
  });
  const now = Date.now();
  const today = (decisions.data ?? []).filter((d) => sameDay(d.created_at, now));
  const trades = today.filter((d) => d.action !== "HOLD");
  const holds = today.length - trades.length;
  const filled = new Map<number, FillRow[]>();
  for (const f of fills.data ?? []) {
    if (!sameDay(f.filled_at, now)) continue;
    filled.set(f.order_id, [...(filled.get(f.order_id) ?? []), f]);
  }
  const sessions = new Set(today.map((d) => d.run_id)).size;
  return (
    <section className="card today">
      <div className="card-head">
        <h2>오늘의 활동</h2>
        <span className="muted">
          판단 {sessions}회 · 매매 결정 {trades.length}건 · 유지 {holds}건
        </span>
      </div>
      <LoadError what="오늘의 활동을" queries={[decisions, fills]} />
      {decisions.isPending ? (
        <SkeletonLines lines={3} label="오늘의 활동 불러오는 중" />
      ) : trades.length === 0 ? (
        <p className="muted today-empty">
          {sessions ? "오늘은 매매 없이 보유 종목을 유지했어요." : "오늘은 아직 판단이 없어요."}
        </p>
      ) : (
        <ul className="today-list">
          {trades.map((d) => {
            const fs = d.order_id == null ? [] : (filled.get(d.order_id) ?? []);
            const qty = fs.reduce((s, f) => s + f.quantity, 0);
            const avg = qty ? fs.reduce((s, f) => s + f.price * f.quantity, 0) / qty : null;
            return (
              <li key={d.id}>
                <time>{fmtHm(d.created_at)}</time>
                <span className={`action-badge ${d.action.toLowerCase()}`}>{ACTION_KO[d.action]}</span>
                <div className="today-main">
                  <Link to={`/stock/${d.symbol}`}>
                    <b>{d.name || d.symbol}</b>
                  </Link>{" "}
                  <span>{d.quantity}주</span>
                  <span className={qty ? "ok" : "muted"}>
                    {qty ? ` · 체결 ${qty}주 @ ${price(avg, cur)}` : ` · ${outcomeText(d.outcome)}`}
                  </span>
                  <p className="muted">{d.rationale}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <Link className="today-more" to="/room">
        판단 근거 전체 보기 →
      </Link>
    </section>
  );
}
