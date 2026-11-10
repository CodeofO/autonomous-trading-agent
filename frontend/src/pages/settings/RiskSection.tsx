import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../../api/client";
import type { RiskConfig, RiskView } from "../../api/types";
import { invalidateNames, MARKET_META, useApi, useCurrency } from "../../market";
import { toast } from "../../ui/toast";
import { riskField, RISK_FIELDS, riskText, validateRisk } from "../../utils/risk";
import { SectionGate } from "./SectionGate";

/** 🛡️ 리스크·주문 — 지금 고른 시장의 리스크 한도 (모든 주문은 브로커로 가기 전에 이 규칙을 통과해야 한다) */
export function RiskSection() {
  const api = useApi();
  const ready = !useQuery({
    queryKey: api.key("risk"),
    queryFn: () => api.get<RiskView>("/api/risk"),
  }).isPending;
  return (
    <SectionGate ready={ready} heights={[380]} label="리스크 한도 불러오는 중">
      <RiskSettings />
    </SectionGate>
  );
}

export function RiskSettings() {
  const qc = useQueryClient();
  const api = useApi();
  const usd = useCurrency() === "USD";
  const q = useQuery({
    queryKey: api.key("risk"),
    queryFn: () => api.get<RiskView>("/api/risk"),
  });
  const [vals, setVals] = useState<Record<string, number>>({});
  const [allowBuys, setAllowBuys] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data) return;
    const c = q.data.config;
    const v: Record<string, number> = {};
    for (const f of RISK_FIELDS)
      v[f.key] = f.pct ? +(((c[f.key] as number) ?? 0) * 100).toFixed(4) : ((c[f.key] as number) ?? 0);
    setVals(v);
    setAllowBuys(c.allow_new_buys);
  }, [q.data]);
  const save = useMutation({
    mutationFn: (body: RiskConfig) => api.put("/api/risk", body),
    // 리스크 한도가 보이는 곳만 (이 폼·구조 화면·전략 실험실)
    onSuccess: () => {
      void invalidateNames(qc, ["risk", "agent-structure", "lab"], api.market);
      const m = MARKET_META[api.market];
      toast(`${m.flag} ${m.name} 리스크 한도를 저장했어요`);
    },
  });
  const preset = useMutation({
    mutationFn: () => api.post<RiskView>("/api/risk/preset"),
    onSuccess: (d) => {
      qc.setQueryData(api.key("risk"), d);
      void invalidateNames(qc, ["agent-structure", "lab"], api.market);
      toast(`${d.strategy?.emoji ?? ""} ${d.strategy?.name ?? "전략"} 기본 한도로 되돌렸어요`);
    },
    onError: (e) => toast.error(`되돌리지 못했어요: ${errorText(e)}`),
  });
  const strategy = q.data?.strategy;
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
      <h2>리스크 한도</h2>
      <p className="muted">
        모든 주문은 브로커로 가기 전에 이 규칙을 통과해야 해요. 장 운영시간 외 주문과 중복 주문은 항상 막아요.
      </p>
      {strategy && (
        <div className="risk-strategy" data-testid="risk-strategy">
          <p>
            <b>
              {strategy.emoji} {strategy.name}
            </b>{" "}
            전략의 한도예요. 전략 실험실에서 주 계좌 전략을 바꾸면 한도도 그 전략 것으로 바뀌고, 여기서 고친
            값은 전략마다 기억해요.
          </p>
          {q.data?.custom ? (
            <p className="muted">
              전략 기본값({riskText(q.data.preset)})과 달라요.{" "}
              <button
                type="button"
                className="btn tiny ghost"
                disabled={preset.isPending}
                onClick={() => preset.mutate()}
              >
                전략 기본값으로
              </button>
            </p>
          ) : (
            <p className="muted">전략 기본값 그대로예요.</p>
          )}
        </div>
      )}
      <form onSubmit={submit} className="form grid-3" aria-label="리스크 설정">
        {RISK_FIELDS.map((field) => riskField(field, usd)).map((f) => (
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
        </div>
      </form>
    </section>
  );
}
