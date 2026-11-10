import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type { Currency, LabDetail, LabRow, LabStyle, LabView } from "../api/types";
import { SkeletonCards } from "../components/Skeleton";
import { MARKET_META, useApi, useCurrency, useMarket } from "../market";
import { toast } from "../ui/toast";
import { ACTION_KO, fmtDateTime, fmtNum, money, price, STATUS_KO } from "../utils/format";
import { riskText } from "../utils/risk";
import "./lab.css";

/** 판단 중이면 자주, 아니면 30초마다 (평가금은 장 밖에서도 움직인다) */
const BUSY_POLL_MS = 5_000;
const IDLE_POLL_MS = 30_000;

const pct = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
const sign = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");

/**
 * ⚗️ 전략 실험실 (D-62) — 시장마다
 * - 주 계좌: 전략 하나로 운용 (실거래 계좌면 그 전략으로 실제 주문)
 * - 시나리오: 켠 전략마다 자기 모의계좌로 같은 시각에 판단 → 성과 비교. 실거래는 막혀 있다
 */
export function Lab() {
  const api = useApi();
  const cur = useCurrency();
  const { market } = useMarket();
  const qc = useQueryClient();
  const key = api.key("lab");
  const q = useQuery({
    queryKey: key,
    queryFn: () => api.get<LabView>("/api/lab"),
    refetchInterval: (query) =>
      query.state.data?.round_running || query.state.data?.running ? BUSY_POLL_MS : IDLE_POLL_MS,
  });
  const [open, setOpen] = useState<string | null>(null);
  const applied = (d: LabView) => qc.setQueryData(key, d);

  const setEnabled = useMutation({
    mutationFn: (enabled: string[]) => api.put<LabView>("/api/lab/scenarios", { enabled }),
    onSuccess: applied,
    onError: (e) => toast.error(`시나리오를 바꾸지 못했어요: ${errorText(e)}`),
  });
  const run = useMutation({
    mutationFn: (id: string) => api.post(`/api/lab/scenarios/${id}/run`),
    onSuccess: (_d, id) => {
      toast(`${styleName(q.data, id)} 시나리오 판단을 시작했어요 — 1~3분쯤 걸려요`);
      void qc.invalidateQueries({ queryKey: key });
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const reset = useMutation({
    mutationFn: (id: string) => api.post<LabView>(`/api/lab/scenarios/${id}/reset`),
    onSuccess: (d, id) => {
      applied(d);
      toast(`${styleName(d, id)} 시나리오 계좌를 처음 금액으로 다시 시작했어요`);
    },
    onError: (e) => toast.error(errorText(e)),
  });

  if (q.isPending) return <SkeletonCards heights={[120, 260, 220]} label="전략 실험실 불러오는 중" />;
  if (q.isError || !q.data)
    return (
      <section className="card">
        <h2>전략 실험실</h2>
        <p className="error">불러오지 못했어요: {errorText(q.error)}</p>
      </section>
    );

  const d = q.data;
  const meta = MARKET_META[market];
  const rows = [...d.rows].sort((a, b) =>
    a.kind !== b.kind ? (a.kind === "main" ? -1 : 1) : (b.return_pct ?? -1e9) - (a.return_pct ?? -1e9),
  );
  const toggle = (id: string, on: boolean) =>
    setEnabled.mutate(on ? [...d.enabled, id] : d.enabled.filter((x) => x !== id));

  return (
    <div className="stack lab-page" data-testid="lab">
      <section className="card lab-intro">
        <h2>
          전략 실험실{" "}
          <small className="muted">
            {meta.flag} {meta.name}
          </small>
        </h2>
        <p className="muted">
          주 계좌는 전략 하나로 운용하고, 나머지 전략은 <b>각자 가상(모의) 계좌</b>로 주 계좌와 같은 판단
          시각에 차례로 판단해 성과를 비교해요. 시나리오 계좌는 실거래로 바꿀 수 없어요 — 실거래에는 아래{" "}
          <b>주 계좌 전략</b> 하나만 쓰여요.
        </p>
        <p className="lab-status">
          {d.round_running || d.running ? (
            <span className="pill lab-busy">
              🔄 판단 중{d.running ? ` — ${styleName(d, d.running)}` : ""}
            </span>
          ) : (
            <span className="muted">
              다음 판단 {d.next_run ? fmtDateTime(d.next_run.at) : "—"} (주 계좌 다음 차례에 시나리오도
              이어서)
            </span>
          )}
          <small className="muted">LLM: {d.llm ?? "없음(규칙 기반)"} · 주 계좌와 같은 모델</small>
        </p>
      </section>

      <section className="card" aria-labelledby="lab-compare">
        <h2 id="lab-compare">성과 비교</h2>
        {d.enabled.length === 0 && (
          <p className="muted">아래에서 시나리오를 켜면 여기서 주 계좌와 나란히 비교해요.</p>
        )}
        <ul className="lab-rows">
          {rows.map((r) => (
            <RowCard
              key={`${r.kind}:${r.id}`}
              r={r}
              cur={cur}
              open={open === r.id && r.kind === "scenario"}
              busy={d.round_running || !!d.running}
              onOpen={() => setOpen(open === r.id ? null : r.id)}
              onRun={() => run.mutate(r.id)}
              onReset={() => {
                if (
                  window.confirm(`${r.name} 시나리오 계좌를 처음 금액으로 다시 시작할까요? (기록은 남아요)`)
                )
                  reset.mutate(r.id);
              }}
            />
          ))}
        </ul>
      </section>

      <MainStrategy data={d} />

      <section className="card" aria-labelledby="lab-scen">
        <h2 id="lab-scen">동시에 시험할 전략</h2>
        <p className="muted">
          켠 전략마다 처음 {money(d.rows[0]?.initial_cash ?? null, cur)} 모의계좌가 생기고, 주 계좌의 리스크
          한도·장외 거래 설정·판단 일정을 그대로 따라요. 판단 세션이 시나리오 수만큼 늘어 LLM 사용량도 그만큼
          늘어요. 최대 {d.max_scenarios}개.
        </p>
        <ul className="lab-styles">
          {d.styles.map((s) => {
            const on = d.enabled.includes(s.id);
            const full = !on && d.enabled.length >= d.max_scenarios;
            return (
              <li key={s.id} className={on ? "on" : ""}>
                <label className="toggle lab-style-toggle">
                  <span className="lab-style-text">
                    <b>
                      {s.emoji} {s.name}
                      {s.id === d.main && <span className="pill">주 계좌</span>}
                    </b>
                    <small className="muted">{s.summary}</small>
                    {s.risk && <small className="lab-risk">🛡️ {riskText(s.risk)}</small>}
                  </span>
                  <input
                    type="checkbox"
                    className="switch"
                    checked={on}
                    disabled={setEnabled.isPending || full}
                    aria-label={`${s.name} 시나리오`}
                    onChange={(e) => toggle(s.id, e.target.checked)}
                  />
                </label>
                <Principles s={s} />
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function styleName(d: LabView | undefined, id: string): string {
  const s = d?.styles.find((x) => x.id === id);
  return s ? `${s.emoji} ${s.name}` : id;
}

function Principles({ s }: { s: LabStyle }) {
  if (!s.policy)
    return <p className="muted lab-policy-note">설정 › 매니저의 투자 정책 문구를 그대로 써요.</p>;
  return (
    <details className="lab-policy">
      <summary>판단 원칙 보기</summary>
      <pre>{s.policy}</pre>
    </details>
  );
}

function RowCard({
  r,
  cur,
  open,
  busy,
  onOpen,
  onRun,
  onReset,
}: {
  r: LabRow;
  cur: Currency;
  open: boolean;
  busy: boolean;
  onOpen: () => void;
  onRun: () => void;
  onReset: () => void;
}) {
  const scenario = r.kind === "scenario";
  return (
    <li className={`lab-row ${r.kind}`} aria-label={`${r.name} ${scenario ? "시나리오" : "주 계좌"}`}>
      <div className="lab-row-head">
        <b className="lab-row-name">
          {r.emoji} {r.name}
        </b>
        {scenario ? (
          <span className="pill">모의 시나리오</span>
        ) : (
          <span className={`pill ${r.live ? "lab-real" : ""}`}>
            {r.live ? "🔴 주 계좌 · 실거래" : "주 계좌 · 모의"}
          </span>
        )}
        {r.running && <span className="pill lab-busy">판단 중</span>}
      </div>
      {r.error ? (
        <p className="error">{r.error}</p>
      ) : (
        <>
          <div className="lab-row-nums">
            <b className={`lab-ret num ${sign(r.return_pct)}`} data-testid={`ret-${r.kind}-${r.id}`}>
              {pct(r.return_pct)}
            </b>
            <span className="num">{money(r.equity ?? null, cur)}</span>
            <small className="muted">
              보유 {r.positions ?? 0}종목 · 투자 {r.invested_pct ?? 0}% · 체결 {r.fills ?? 0}건 · 판단{" "}
              {r.runs ?? 0}회
            </small>
          </div>
          {r.risk && <small className="lab-risk">🛡️ {r.risk.text}</small>}
          {(r.top_positions?.length ?? 0) > 0 && (
            <ul className="lab-top">
              {r.top_positions?.map((p) => (
                <li key={p.symbol} className="chip">
                  {p.name || p.symbol} {p.weight}% <span className={sign(p.pnl_pct)}>{pct(p.pnl_pct)}</span>
                </li>
              ))}
            </ul>
          )}
          {r.last_run && (
            <p className="lab-last">
              <small className="muted">
                마지막 판단 #{r.last_run.id} · {r.last_run.at ? fmtDateTime(r.last_run.at) : "—"}
                {r.last_run.status === "FAILED" ? " · 실패" : ""}
              </small>
              <span>{r.last_run.summary || "요약 없음"}</span>
            </p>
          )}
        </>
      )}
      {scenario && (
        <div className="btn-row lab-actions">
          <button type="button" className="btn tiny" disabled={busy || r.running} onClick={onRun}>
            ▶ 지금 판단
          </button>
          <button type="button" className="btn tiny ghost" aria-expanded={open} onClick={onOpen}>
            {open ? "접기" : "자세히"}
          </button>
          <button type="button" className="btn tiny ghost" disabled={busy || r.running} onClick={onReset}>
            다시 시작
          </button>
        </div>
      )}
      {open && <ScenarioDetail id={r.id} cur={cur} />}
    </li>
  );
}

function ScenarioDetail({ id, cur }: { id: string; cur: Currency }) {
  const api = useApi();
  const q = useQuery({
    queryKey: api.key("lab", id),
    queryFn: () => api.get<LabDetail>(`/api/lab/scenarios/${id}`),
    refetchInterval: IDLE_POLL_MS,
  });
  if (q.isPending) return <p className="muted">불러오는 중…</p>;
  if (q.isError || !q.data) return <p className="error">불러오지 못했어요: {errorText(q.error)}</p>;
  const d = q.data;
  return (
    <div className="lab-detail">
      <h4>보유 종목</h4>
      {d.positions.length === 0 ? (
        <p className="muted">없음</p>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>종목</th>
              <th className="r">수량</th>
              <th className="r">평균가</th>
              <th className="r">현재가</th>
              <th className="r">수익률</th>
            </tr>
          </thead>
          <tbody>
            {d.positions.map((p) => (
              <tr key={p.symbol}>
                <td>{p.name || p.symbol}</td>
                <td className="r">{fmtNum(p.quantity)}</td>
                <td className="r">{price(p.avg_price, cur)}</td>
                <td className="r">{price(p.last_price, cur)}</td>
                <td className={`r ${sign(p.pnl_pct)}`}>{pct(p.pnl_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>최근 판단</h4>
      {d.runs.length === 0 ? (
        <p className="muted">아직 판단 기록이 없어요.</p>
      ) : (
        <ul className="lab-runs">
          {d.runs.map((r) => (
            <li key={r.id}>
              <small className="muted">
                #{r.id} · {r.started_at ? fmtDateTime(r.started_at) : "—"} · {r.status}
              </small>
              <span>{r.summary || "요약 없음"}</span>
            </li>
          ))}
        </ul>
      )}
      <h4>최근 주문</h4>
      {d.orders.length === 0 ? (
        <p className="muted">없음</p>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>접수</th>
              <th>종목</th>
              <th>구분</th>
              <th className="r">수량</th>
              <th>상태</th>
              <th className="r">체결</th>
            </tr>
          </thead>
          <tbody>
            {d.orders.map((o) => (
              <tr key={o.id}>
                <td>{o.created_at ? fmtDateTime(o.created_at) : "—"}</td>
                <td>{o.name || o.symbol}</td>
                <td className={o.side === "BUY" ? "up" : "down"}>{ACTION_KO[o.side] ?? o.side}</td>
                <td className="r">{fmtNum(o.quantity)}</td>
                <td>{STATUS_KO[o.status] ?? o.status}</td>
                <td className="r">
                  {o.filled_qty > 0 ? `${fmtNum(o.filled_qty)} @ ${price(o.avg_fill_price, cur)}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** 🎯 주 계좌 전략 — 고른 뒤 '적용'. 실거래 계좌면 다음 판단부터 그 전략으로 실제 주문이 나간다 */
function MainStrategy({ data }: { data: LabView }) {
  const api = useApi();
  const qc = useQueryClient();
  const [pick, setPick] = useState(data.main);
  const save = useMutation({
    mutationFn: (id: string) => api.put<LabView>("/api/lab/main", { id }),
    onSuccess: (d) => {
      qc.setQueryData(api.key("lab"), d);
      void qc.invalidateQueries({ queryKey: api.key("agent-structure") });
      void qc.invalidateQueries({ queryKey: api.key("risk") });
      toast(`주 계좌 전략을 ${styleName(d, d.main)}(으)로 바꿨어요 — 다음 판단부터 적용돼요 ✓`);
    },
    onError: (e) => toast.error(`바꾸지 못했어요: ${errorText(e)}`),
  });
  const dirty = pick !== data.main;
  return (
    <section className="card" aria-labelledby="lab-main">
      <h2 id="lab-main">주 계좌 전략</h2>
      <p className="muted">
        주 계좌 매니저가 따를 전략이에요. 시나리오 비교를 보고 가장 마음에 드는 전략을 여기서 고르세요. 판단
        원칙과 함께 리스크 한도(손절·익절·최소 보유 기간 등)도 전략마다 달라요.
      </p>
      <div className="lab-main-pick" role="radiogroup" aria-label="주 계좌 전략">
        {data.styles.map((s) => (
          <label key={s.id} className={`lab-main-opt ${pick === s.id ? "on" : ""}`}>
            <input
              type="radio"
              name="lab-main"
              value={s.id}
              checked={pick === s.id}
              onChange={() => setPick(s.id)}
            />
            <span>
              {s.emoji} {s.name}
            </span>
          </label>
        ))}
      </div>
      {dirty && (
        <p className="muted lab-main-risk">
          🛡️ 리스크 한도도 이 전략 것으로 바뀌어요
          {(() => {
            const r = data.styles.find((x) => x.id === pick)?.risk;
            return r ? `: ${riskText(r)}` : "";
          })()}
          . 전에 이 전략으로 운용하며 고쳐 둔 한도가 있으면 그 값을 써요.
        </p>
      )}
      {data.live && dirty && (
        <p className="error">
          🔴 주 계좌가 실거래 계좌예요 — 적용하면 다음 판단부터 이 전략으로 실제 주문이 나가요.
        </p>
      )}
      <div className="btn-row">
        <button
          type="button"
          className={`btn ${data.live ? "kill" : "primary"}`}
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(pick)}
        >
          {save.isPending ? "적용하는 중…" : "적용"}
        </button>
        {dirty && (
          <button type="button" className="btn ghost" onClick={() => setPick(data.main)}>
            취소
          </button>
        )}
      </div>
    </section>
  );
}
