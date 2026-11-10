import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, type MarketApi } from "../../api/client";
import type { Strategy } from "../../api/types";
import { invalidateNames, MARKET_META, useApi } from "../../market";
import { toast } from "../../ui/toast";
import { SectionGate } from "./SectionGate";

interface AgentSettingsData {
  name: string;
  emoji: string;
  policy: string;
  default_policy: string;
}

/** 매니저 이름·정책 (설정 주제와 카드가 같은 정의 — 같은 캐시, 요청은 한 번) */
const agentSettingsQuery = (api: MarketApi) => ({
  queryKey: api.key("agent-settings"),
  queryFn: () => api.get<AgentSettingsData>("/api/agent/settings"),
});

/** 🦉 매니저 — 지금 고른 시장 매니저의 이름·운용 방침, 메모 카드, 규칙 전략 */
export function ManagerSection() {
  const api = useApi();
  const ready = !useQuery(agentSettingsQuery(api)).isPending;
  return (
    <SectionGate ready={ready} heights={[400, 160, 220]} label="매니저 설정 불러오는 중">
      <div className="stack">
        <AgentSettings />
        <Link to="/memo" className="card link-card settings-link-card">
          <b>메모 카드 →</b>
          <small className="muted">매니저가 기억할 관심·주목·피할 종목과 운용 지시를 카드로 관리해요</small>
        </Link>
        <StrategySettings />
      </div>
    </SectionGate>
  );
}

function AgentSettings() {
  const qc = useQueryClient();
  const api = useApi();
  const q = useQuery(agentSettingsQuery(api));
  const [form, setForm] = useState({ name: "", emoji: "", policy: "" });
  useEffect(() => {
    if (q.data) setForm({ name: q.data.name, emoji: q.data.emoji, policy: q.data.policy });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.put("/api/agent/settings", form),
    // 매니저 이름·이모지·정책이 보이는 곳만 (상단 바·대시보드는 system, 구조 화면)
    onSuccess: () => {
      void invalidateNames(qc, ["agent-settings", "system", "agent-structure"], api.market);
      const m = MARKET_META[api.market];
      toast(`${m.flag} ${m.name} 매니저 설정을 저장했어요 다음 판단부터 적용돼요.`);
    },
  });
  return (
    <section className="card">
      <h2>매니저 캐릭터 · 운용 방침</h2>
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
          {save.isError && <span className="error">{errorText(save.error)}</span>}
        </div>
      </form>
    </section>
  );
}

function StrategySettings() {
  const qc = useQueryClient();
  const api = useApi();
  const q = useQuery({
    queryKey: api.key("strategies"),
    queryFn: () => api.get<Strategy[]>("/api/strategies"),
  });
  return (
    <section className="card">
      <h2>규칙 전략</h2>
      <p className="muted">LLM 이 없을 때 규칙 기반 판단과 백테스트에 쓰는 예시 전략이에요.</p>
      {q.data?.map((s) => (
        <StrategyRow
          key={s.id}
          s={s}
          // 전략 목록(이 카드·백테스트)과 구조 화면만
          onSaved={() => invalidateNames(qc, ["strategies", "agent-structure"], api.market)}
        />
      ))}
    </section>
  );
}

function StrategyRow({ s, onSaved }: { s: Strategy; onSaved: () => void }) {
  const api = useApi();
  const [params, setParams] = useState(s.params);
  const save = useMutation({
    mutationFn: (body: { enabled?: boolean; params?: Record<string, number> }) =>
      api.put(`/api/strategies/${s.id}`, body),
    onSuccess: (_d, body) => {
      onSaved();
      toast(
        body.enabled === undefined
          ? `${s.name} 값을 적용했어요`
          : `${s.name} 전략을 ${body.enabled ? "켰어요" : "껐어요"}`,
      );
    },
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
