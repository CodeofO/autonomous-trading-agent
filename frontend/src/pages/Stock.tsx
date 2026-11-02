import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api/client";
import type { Bar, Decision, Marker } from "../api/types";
import { CandleChart } from "../components/charts";
import { ACTION_KO, fmtDateTime, fmtNum, signClass } from "../utils/format";

interface Research {
  name: string;
  financials: {
    available?: boolean;
    reason?: string;
    error?: string;
    source?: string;
    reports?: Record<string, number | string | null>[];
    valuation?: Record<string, number | string | null>;
  };
  news: { items?: { title: string; description: string; pubDate: string; link: string }[] };
  disclosures: { items?: { report_nm: string; rcept_dt: string }[] };
  supply?: {
    error?: string;
    investor_net_buy_daily?: {
      date: string;
      foreigner: number | null;
      institution: number | null;
      individual: number | null;
    }[];
    short_selling_volume_rate_pct?: { date: string; rate: number | null }[];
    margin_loan_balance_rate_pct?: { date: string; rate: number | null }[];
  };
  status?: {
    error?: string;
    warnings?: { warningType: string; startDate: string; endDate: string | null }[];
    trading_suspended?: boolean;
    price_limits?: { upper: number; lower: number };
    spread_pct?: number | null;
    bid_ask_volume_ratio?: number | null;
    orderbook_top5?: { asks: [number, number][]; bids: [number, number][] };
  };
}

const WARN_KO: Record<string, string> = {
  OVERHEATED: "단기과열",
  INVESTMENT_WARNING: "투자경고",
  INVESTMENT_DANGER: "투자위험",
  INVESTMENT_CAUTION: "투자주의",
  VI_STATIC: "정적 VI",
  VI_DYNAMIC: "동적 VI",
  LIQUIDATION_TRADING: "정리매매",
};

function SupplyPanel({ r }: { r: Research }) {
  const sup = r.supply;
  const st = r.status;
  if (!sup && !st) return null;
  const daily = sup?.investor_net_buy_daily ?? [];
  const sum = (k: "foreigner" | "institution" | "individual") => daily.reduce((a, d) => a + (d[k] ?? 0), 0);
  const short = sup?.short_selling_volume_rate_pct?.[0]?.rate;
  const margin = sup?.margin_loan_balance_rate_pct?.[0]?.rate;
  return (
    <section className="card">
      <h2>
        수급·상태 <small className="muted">(토스증권, 최근 {daily.length}거래일 순매수 수량)</small>
      </h2>
      {sup?.error && <p className="muted">{sup.error}</p>}
      {daily.length > 0 && (
        <div className="flow">
          {(
            [
              ["foreigner", "외국인"],
              ["institution", "기관"],
              ["individual", "개인"],
            ] as const
          ).map(([k, label]) => (
            <div key={k}>
              <span className="muted">{label}</span>
              <b className={signClass(sum(k))}>{fmtNum(sum(k))}주</b>
            </div>
          ))}
        </div>
      )}
      <div className="flow">
        <div>
          <span className="muted">공매도 비중</span>
          <b>{short == null ? "—" : `${short}%`}</b>
        </div>
        <div>
          <span className="muted">신용잔고율</span>
          <b>{margin == null ? "—" : `${margin}%`}</b>
        </div>
        <div>
          <span className="muted">호가 스프레드 · 매수/매도 잔량비</span>
          <b>
            {st?.spread_pct == null ? "—" : `${st.spread_pct}%`} · {st?.bid_ask_volume_ratio ?? "—"}
          </b>
        </div>
      </div>
      {st?.price_limits && (
        <p className="muted">
          상한가 {fmtNum(st.price_limits.upper)} · 하한가 {fmtNum(st.price_limits.lower)}
          {st.trading_suspended ? " · ⛔ 거래정지" : ""}
        </p>
      )}
      <div>
        {(st?.warnings ?? []).map((w, i) => (
          <span key={i} className="warn-badge" title={`${w.startDate} ~ ${w.endDate ?? ""}`}>
            ⚠️ {WARN_KO[w.warningType] ?? w.warningType}
          </span>
        ))}
        {st && (st.warnings ?? []).length === 0 && <span className="muted">매수 유의사항 없음</span>}
      </div>
    </section>
  );
}

const VAL_LABEL: Record<string, string> = {
  market_cap_eok: "시가총액(억)",
  per: "PER(배)",
  pbr: "PBR(배)",
  roe_pct: "ROE(%)",
  earnings_basis: "이익 기준",
};

const FIN_ROWS: [string, string][] = [
  ["revenue", "매출"],
  ["operating_income", "영업이익"],
  ["net_income", "순이익"],
  ["depreciation", "감가상각비"],
  ["ebitda", "EBITDA"],
  ["operating_cash_flow", "영업현금흐름"],
  ["capex", "CAPEX"],
  ["total_assets", "자산"],
  ["total_liabilities", "부채"],
  ["total_equity", "자본"],
  ["op_margin_pct", "영업이익률(%)"],
  ["debt_ratio_pct", "부채비율(%)"],
  ["yoy_revenue_pct", "매출 YoY(%)"],
];

export function Stock() {
  const { symbol = "" } = useParams();
  const qc = useQueryClient();
  const candles = useQuery({
    queryKey: ["candles", symbol],
    queryFn: () =>
      get<{ name: string; bars: Bar[]; markers: Marker[] }>(`/api/market/${symbol}/candles?days=180`),
  });
  const research = useQuery({
    queryKey: ["research", symbol],
    queryFn: () => get<Research>(`/api/market/${symbol}/research`),
  });
  const decisions = useQuery({
    queryKey: ["decisions", symbol],
    queryFn: () => get<Decision[]>(`/api/agent/decisions?symbol=${symbol}&limit=30`),
  });
  const watch = useMutation({
    mutationFn: () => post("/api/watchlist", { symbol }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const fin = research.data?.financials;
  const last = candles.data?.bars.at(-1);

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>
            {candles.data?.name || symbol} <small className="muted">{symbol}</small>
          </h2>
          <div className="btn-row">
            {last && (
              <span className="chip">
                종가 {fmtNum(last.close)} ({last.time})
              </span>
            )}
            <button className="btn ghost" onClick={() => watch.mutate()}>
              ⭐ 관심종목 추가
            </button>
          </div>
        </div>
        {candles.data && <CandleChart bars={candles.data.bars} markers={candles.data.markers} />}
        <p className="muted legend">▲▼ 체결 · ● AI 결정 (빨강=상승/매수, 파랑=하락/매도)</p>
      </section>

      {research.data && <SupplyPanel r={research.data} />}

      <div className="two-col">
        <section className="card">
          <h2>재무 요약 {fin?.source && <small className="muted">({fin.source}, 억원)</small>}</h2>
          {!fin ? (
            <p className="muted">불러오는 중…</p>
          ) : fin.error || fin.available === false ? (
            <p className="muted">{fin.error ?? fin.reason ?? "재무 데이터 없음"}</p>
          ) : (
            <>
              <div className="valuation">
                {Object.entries(fin.valuation ?? {}).map(([k, v]) => (
                  <div key={k}>
                    <span className="muted">{VAL_LABEL[k] ?? k}</span>
                    <b>{typeof v === "number" ? fmtNum(v, 2) : (v ?? "—")}</b>
                  </div>
                ))}
              </div>
              <table className="table compact">
                <thead>
                  <tr>
                    <th />
                    {fin.reports?.map((r) => (
                      <th key={String(r.period)} className="r">
                        {String(r.period)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {FIN_ROWS.map(([k, label]) => (
                    <tr key={k}>
                      <td>{label}</td>
                      {fin.reports?.map((r) => (
                        <td key={String(r.period)} className="r">
                          {typeof r[k] === "number" ? fmtNum(r[k] as number, 1) : "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>

        <section className="card">
          <h2>뉴스</h2>
          <ul className="news">
            {(research.data?.news.items ?? []).map((n, i) => (
              <li key={i}>
                {n.link.startsWith("http") ? (
                  <a href={n.link} target="_blank" rel="noreferrer noopener">
                    {n.title}
                  </a>
                ) : (
                  <b>{n.title}</b>
                )}
                <p className="muted">{n.description}</p>
                <small className="muted">{n.pubDate}</small>
              </li>
            ))}
            {research.data && (research.data.news.items ?? []).length === 0 && (
              <p className="muted">뉴스 없음</p>
            )}
          </ul>
          <h2>공시</h2>
          <ul className="disclosures">
            {(research.data?.disclosures.items ?? []).map((d, i) => (
              <li key={i}>
                <span className="muted">{d.rcept_dt}</span> {d.report_nm}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card">
        <h2>AI 결정 이력</h2>
        {(decisions.data ?? []).length === 0 && (
          <p className="muted empty">이 종목에 대한 결정이 아직 없어요.</p>
        )}
        <ul className="decision-list">
          {decisions.data?.map((d) => (
            <li key={d.id}>
              <span className={`action-badge ${d.action.toLowerCase()}`}>{ACTION_KO[d.action]}</span>
              <span className="muted">
                {fmtDateTime(d.created_at)} · 확신도 {Math.round(d.confidence * 100)}%
              </span>
              <p>{d.rationale}</p>
              {d.evidence.length > 0 && <small className="muted">근거: {d.evidence.join(" · ")}</small>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
