import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, errorText, get, post, put } from "../api/client";
import type { RiskConfig, Strategy } from "../api/types";

export function Settings() {
  return (
    <div className="stack">
      <AgentSettings />
      <RiskSettings />
      <div className="two-col">
        <StrategySettings />
        <Watchlist />
      </div>
    </div>
  );
}

function AgentSettings() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["agent-settings"],
    queryFn: () =>
      get<{ name: string; emoji: string; policy: string; default_policy: string }>("/api/agent/settings"),
  });
  const [form, setForm] = useState({ name: "", emoji: "", policy: "" });
  useEffect(() => {
    if (q.data) setForm({ name: q.data.name, emoji: q.data.emoji, policy: q.data.policy });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => put("/api/agent/settings", form),
    onSuccess: () => qc.invalidateQueries(),
  });
  return (
    <section className="card">
      <h2>🦉 에이전트 (매니저 캐릭터 · 투자 정책)</h2>
      <p className="muted">
        투자 정책은 매 판단 세션의 시스템 프롬프트에 들어가요. 리스크 한도는 아래 설정이 코드로 강제하므로,
        정책에 뭐라고 써도 한도를 넘을 수 없어요.
      </p>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
        className="form"
      >
        <div className="row">
          <label>
            이름
            <input
              value={form.name}
              maxLength={20}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            이모지
            <input
              value={form.emoji}
              maxLength={8}
              onChange={(e) => setForm({ ...form, emoji: e.target.value })}
              className="emoji-input"
            />
          </label>
        </div>
        <label>
          투자 정책
          <textarea
            rows={7}
            value={form.policy}
            onChange={(e) => setForm({ ...form, policy: e.target.value })}
          />
        </label>
        <div className="btn-row">
          <button className="btn primary" disabled={save.isPending}>
            저장
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={() => q.data && setForm({ ...form, policy: q.data.default_policy })}
          >
            기본 정책으로
          </button>
          {save.isSuccess && <span className="ok">저장됨 ✓</span>}
          {save.isError && <span className="error">{errorText(save.error)}</span>}
        </div>
      </form>
    </section>
  );
}

const RISK_FIELDS: { key: keyof RiskConfig; label: string; pct?: boolean; hint: string }[] = [
  { key: "max_position_weight", label: "종목당 최대 비중", pct: true, hint: "예: 20" },
  { key: "max_order_amount", label: "1회 주문 최대 금액(원)", hint: "예: 1,000,000" },
  { key: "daily_loss_limit", label: "일일 최대 손실", pct: true, hint: "음수, 예: -3 → 당일 신규 매수 중단" },
  { key: "stop_loss", label: "종목별 손절", pct: true, hint: "음수, 예: -5" },
  { key: "take_profit", label: "종목별 익절", pct: true, hint: "양수, 예: 10" },
  { key: "min_order_interval_minutes", label: "같은 종목 최소 주문 간격(분)", hint: "예: 60" },
  { key: "max_positions", label: "최대 보유 종목 수", hint: "예: 10" },
  { key: "min_cash_ratio", label: "최소 현금 비중", pct: true, hint: "예: 5" },
];

export function validateRisk(v: Record<string, number>): string | null {
  if (!(v.max_position_weight > 0 && v.max_position_weight <= 100))
    return "종목당 최대 비중은 0~100% 사이여야 해요";
  if (!(v.max_order_amount > 0)) return "1회 주문 최대 금액은 0보다 커야 해요";
  if (!(v.daily_loss_limit < 0 && v.daily_loss_limit >= -100)) return "일일 최대 손실은 음수여야 해요";
  if (!(v.stop_loss < 0 && v.stop_loss >= -100)) return "손절은 음수여야 해요";
  if (!(v.take_profit > 0)) return "익절은 양수여야 해요";
  if (!(v.min_order_interval_minutes >= 0)) return "최소 주문 간격은 0 이상이어야 해요";
  if (!(v.max_positions >= 1 && Number.isInteger(v.max_positions)))
    return "최대 보유 종목 수는 1 이상의 정수여야 해요";
  if (!(v.min_cash_ratio >= 0 && v.min_cash_ratio < 100)) return "최소 현금 비중은 0~100% 사이여야 해요";
  return null;
}

export function RiskSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["risk"], queryFn: () => get<{ config: RiskConfig }>("/api/risk") });
  const [vals, setVals] = useState<Record<string, number>>({});
  const [allowBuys, setAllowBuys] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const c = q.data.config;
    const v: Record<string, number> = {};
    for (const f of RISK_FIELDS)
      v[f.key] = f.pct ? +((c[f.key] as number) * 100).toFixed(4) : (c[f.key] as number);
    setVals(v);
    setAllowBuys(c.allow_new_buys);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (body: RiskConfig) => put("/api/risk", body),
    onSuccess: () => qc.invalidateQueries(),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const msg = validateRisk(vals);
    setErr(msg);
    if (msg) return;
    const body = { allow_new_buys: allowBuys } as Record<string, number | boolean>;
    for (const f of RISK_FIELDS) body[f.key] = f.pct ? vals[f.key] / 100 : vals[f.key];
    save.mutate(body as unknown as RiskConfig);
  };
  return (
    <section className="card">
      <h2>🛡️ 리스크 한도</h2>
      <p className="muted">
        모든 주문은 브로커로 가기 전에 이 규칙을 통과해야 해요. 장 운영시간 외 주문과 중복 주문은 항상 막아요.
      </p>
      <form onSubmit={submit} className="form grid-3" aria-label="리스크 설정">
        {RISK_FIELDS.map((f) => (
          <label key={f.key}>
            {f.label}
            {f.pct ? " (%)" : ""}
            <input
              type="number"
              step="any"
              value={vals[f.key] ?? ""}
              placeholder={f.hint}
              aria-label={f.label}
              onChange={(e) => setVals({ ...vals, [f.key]: Number(e.target.value) })}
            />
          </label>
        ))}
        <label className="toggle">
          <input type="checkbox" checked={allowBuys} onChange={(e) => setAllowBuys(e.target.checked)} />
          신규 매수 허용
        </label>
        <div className="btn-row full">
          <button className="btn primary" disabled={save.isPending}>
            저장
          </button>
          {err && <span className="error">{err}</span>}
          {save.isError && <span className="error">{errorText(save.error)}</span>}
          {save.isSuccess && !err && <span className="ok">저장됨 ✓</span>}
        </div>
      </form>
    </section>
  );
}

function StrategySettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["strategies"], queryFn: () => get<Strategy[]>("/api/strategies") });
  return (
    <section className="card">
      <h2>📏 규칙 전략</h2>
      <p className="muted">LLM 이 없을 때 규칙 기반 판단과 백테스트에 쓰는 예시 전략이에요.</p>
      {q.data?.map((s) => <StrategyRow key={s.id} s={s} onSaved={() => qc.invalidateQueries()} />)}
    </section>
  );
}

function StrategyRow({ s, onSaved }: { s: Strategy; onSaved: () => void }) {
  const [params, setParams] = useState(s.params);
  const save = useMutation({
    mutationFn: (body: { enabled?: boolean; params?: Record<string, number> }) =>
      put(`/api/strategies/${s.id}`, body),
    onSuccess: onSaved,
  });
  return (
    <div className="strategy">
      <div className="card-head">
        <b>{s.name}</b>
        <label className="toggle">
          <input
            type="checkbox"
            checked={s.enabled}
            onChange={(e) => save.mutate({ enabled: e.target.checked })}
          />
          사용
        </label>
      </div>
      <div className="row">
        {Object.entries(s.schema.properties).map(([k, spec]) => (
          <label key={k}>
            {k}
            <input
              type="number"
              min={spec.minimum}
              max={spec.maximum}
              value={params[k] ?? ""}
              onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })}
            />
          </label>
        ))}
        <button className="btn tiny" onClick={() => save.mutate({ params })}>
          적용
        </button>
      </div>
      {save.isError && <p className="error">{errorText(save.error)}</p>}
    </div>
  );
}

function Watchlist() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["watchlist"],
    queryFn: () => get<{ symbol: string; name: string; close: number | null }[]>("/api/watchlist"),
  });
  const [sym, setSym] = useState("");
  const add = useMutation({
    mutationFn: () => post("/api/watchlist", { symbol: sym.trim() }),
    onSuccess: () => {
      setSym("");
      qc.invalidateQueries({ queryKey: ["watchlist"] });
    },
  });
  const remove = useMutation({
    mutationFn: (s: string) => del(`/api/watchlist/${s}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  return (
    <section className="card">
      <h2>⭐ 관심 종목</h2>
      <p className="muted">매니저가 매 세션 포트폴리오와 함께 참고하는 목록이에요.</p>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (sym.trim()) add.mutate();
        }}
      >
        <input
          value={sym}
          onChange={(e) => setSym(e.target.value)}
          placeholder="종목코드 (예: 005930 / MOCK01)"
        />
        <button className="btn tiny">추가</button>
      </form>
      {add.isError && <p className="error">{errorText(add.error)}</p>}
      <ul className="watch">
        {q.data?.map((w) => (
          <li key={w.symbol}>
            <Link to={`/stock/${w.symbol}`}>
              {w.name || w.symbol} <small className="muted">{w.symbol}</small>
            </Link>
            <button className="link" onClick={() => remove.mutate(w.symbol)}>
              삭제
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
