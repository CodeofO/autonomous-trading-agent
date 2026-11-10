import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, del, errorText, post, put } from "../../api/client";
import type {
  LlmModelOption,
  LlmPaidProfileInfo,
  LlmProfileName,
  LlmProfileInfo,
  LlmProfiles,
  LlmTestApi,
  LlmTestCheck,
  LlmTestEndpoint,
  LlmTestField,
  LlmTestSource,
  LlmTestValues,
} from "../../api/types";
import { Modal } from "../../components/Modal";
import { useLlmProfile, useSystem } from "../../hooks";
import { invalidateNames, useMarket } from "../../market";
import { toast } from "../../ui/toast";
import { fmtDateTime } from "../../utils/format";
import {
  apiLabel,
  groupModels,
  MODEL_GROUPS,
  modelOptionText,
  modelPriceText,
  PROFILE_KO,
  REASONING_EFFORTS,
  TEST_API_OPTIONS,
  testSourceText,
} from "../../utils/llm";
import {
  llmProfileQuery,
  TEST_URL,
  testEndpointQuery,
  US_PROFILE_KEY,
  US_PROFILE_URL,
  usLlmProfileQuery,
} from "../settingsQueries";
import { OpenRouterRegistry } from "./OpenRouterRegistry";
import { SectionGate } from "./SectionGate";

/**
 * 🤖 LLM 모델 — 무료/유료(국장·공통), 유료 모델 고르기(OpenRouter 목록 포함), 테스트용(무료) LLM, 미장 매니저 LLM.
 * 카드 안 내용(무료/유료·테스트 LLM·미장)이 따로 도착하며 밀리지 않도록 다 받을 때까지 자리 표시 한 번
 */
export function LlmSection() {
  const { available } = useMarket();
  const withUs = available.includes("US");
  const ready = [
    useQuery(llmProfileQuery).isPending,
    useQuery(testEndpointQuery).isPending,
    useQuery({ ...usLlmProfileQuery, enabled: withUs }).isLoading,
  ].every((p) => !p);
  return (
    <SectionGate ready={ready} heights={[520]} label="LLM 설정 불러오는 중">
      <LlmProfileSettings />
    </SectionGate>
  );
}

const PROFILE_ORDER: LlmProfileName[] = ["free", "paid"];

function switchErrorText(e: unknown): string {
  const text = errorText(e);
  if (e instanceof ApiError && e.status === 409) return `${text} — 잠시 후 다시 시도해 주세요.`;
  return text;
}

/** 모델 이름을 보여 주는 화면(상단 칩·구조·비용·LLM 설정·시장 목록)을 두 시장 모두 다시 받는다 */
function useRefreshLlmViews() {
  const qc = useQueryClient();
  return () => {
    invalidateNames(qc, ["system", "llm-profile", "agent-structure", "llm-costs", "markets", "overview"]);
  };
}

/** 서버가 돌려준 새 LLM 설정을 캐시에 넣고, 모델 이름을 보여 주는 화면을 다시 받는다 */
function useApplyLlmProfile() {
  const qc = useQueryClient();
  const refresh = useRefreshLlmViews();
  return (data: LlmProfiles) => {
    qc.setQueryData(["llm-profile"], data);
    refresh();
  };
}

/** LLM 무료(.env LLM_TEST_*) / 유료(LLM_*) 전환. 유료로 바꿀 때만 한 번 더 확인 */
export function LlmProfileSettings() {
  const q = useLlmProfile();
  const apply = useApplyLlmProfile();
  const { available } = useMarket();
  // 이 전환(/api/llm/profile)은 국장 매니저 기준 — 미장 매니저는 아래 따로
  const { data: sys } = useSystem("KR");
  const [confirm, setConfirm] = useState(false);
  const change = useMutation({
    mutationFn: (active: LlmProfileName) => put<LlmProfiles>("/api/llm/profile", { active }),
    onSuccess: (data) => {
      setConfirm(false);
      apply(data);
      const now = data.profiles[data.active];
      toast(
        `${PROFILE_KO[data.active]} 모델(${now?.model ?? "-"})로 바꿨어요 ✓ 다음 판단·답장부터 적용돼요.`,
      );
    },
  });

  const data = q.data;
  const pick = (p: LlmProfileName) => {
    if (!data || p === data.active || change.isPending) return;
    change.reset();
    if (p === "paid") setConfirm(true);
    else change.mutate(p);
  };
  const paid = data?.profiles.paid;
  const interval = sys?.agent_interval_minutes ?? 0;

  return (
    <section className="card llm-card">
      <h2>LLM 모델</h2>
      <p className="muted">
        매니저가 판단·대화에 쓰는 모델이에요. 무료는 .env 의 LLM_TEST_*, 유료는 LLM_* 설정을 써요. 바꾸면 다음
        판단·답장부터 적용돼요.
      </p>
      {q.isLoading && <p className="muted">불러오는 중…</p>}
      {q.isError && <p className="error">{errorText(q.error)}</p>}
      {data && (
        <div className="seg llm-seg" role="group" aria-label="LLM 모델 선택">
          {PROFILE_ORDER.map((key) => {
            const p = data.profiles[key];
            const on = data.active === key;
            const api = apiLabel(p.api);
            return (
              <button
                key={key}
                type="button"
                className={on ? `active ${key}` : key}
                aria-pressed={on}
                disabled={!p.configured || change.isPending}
                onClick={() => pick(key)}
              >
                <b>{p.label || PROFILE_KO[key]}</b>
                {p.configured ? (
                  <>
                    <span className="llm-seg-model">{p.model ?? "-"}</span>
                    {api && <small className="muted">{api}</small>}
                  </>
                ) : (
                  <small className="muted">미설정 — .env 의 {p.env} 를 채우세요</small>
                )}
              </button>
            );
          })}
        </div>
      )}
      {data && <PaidModelPicker paid={data.profiles.paid} active={data.active} busy={change.isPending} />}
      {available.includes("US") && <UsLlmProfileToggle />}
      <div className="llm-status" aria-live="polite">
        {change.isPending && !confirm && <p className="muted">바꾸는 중…</p>}
        {change.isError && !confirm && <p className="error">{switchErrorText(change.error)}</p>}
        {data?.active === "paid" && (
          <p className="push-note warn">유료 모델을 쓰는 중이에요 — 호출마다 요금이 나가요.</p>
        )}
      </div>
      {confirm && paid && (
        <Modal
          title="유료 모델로 바꿀까요?"
          tone="danger"
          onClose={() => !change.isPending && setConfirm(false)}
          actions={
            <>
              <button className="btn ghost" onClick={() => setConfirm(false)} disabled={change.isPending}>
                취소
              </button>
              <button
                className="btn primary"
                onClick={() => change.mutate("paid")}
                disabled={change.isPending}
              >
                {change.isPending ? "바꾸는 중…" : "유료로 바꾸기"}
              </button>
            </>
          }
        >
          <p>
            유료 모델은 호출마다 실제 요금이 나가요.{" "}
            {interval > 0
              ? `지금 일정(장중 ${interval}분마다 판단)이면 판단 세션과 채팅 답장마다 호출돼요.`
              : "판단 세션과 채팅 답장마다 호출돼요."}
          </p>
          <p className="muted">
            {paid.model ?? "-"}
            {apiLabel(paid.api) ? ` · ${apiLabel(paid.api)}` : ""} — 언제든 다시 무료로 돌릴 수 있어요.
          </p>
          <p>
            <Link to="/costs">LLM 요금 보기 →</Link>
          </p>
          {change.isError && <p className="error">{switchErrorText(change.error)}</p>}
        </Modal>
      )}
      <TestEndpointSettings />
    </section>
  );
}

/** 미장 매니저 LLM 무료/유료 (GET·PUT /api/us/llm/profile). 국장과 따로 고르고, 유료로 바꿀 때만 한 번 더 확인 */
export function UsLlmProfileToggle() {
  const qc = useQueryClient();
  const refresh = useRefreshLlmViews();
  const { data: sys } = useSystem("US", { refetchInterval: 30_000 });
  const q = useQuery(usLlmProfileQuery);
  const [confirm, setConfirm] = useState(false);
  const change = useMutation({
    mutationFn: (active: LlmProfileName) => put<LlmProfiles>(US_PROFILE_URL, { active }),
    onSuccess: (data) => {
      setConfirm(false);
      qc.setQueryData(US_PROFILE_KEY, data);
      refresh();
      toast(`미장 매니저를 ${PROFILE_KO[data.active]} 모델로 바꿨어요 ✓ 다음 판단·답장부터 적용돼요.`);
    },
  });

  const data = q.data;
  const pick = (p: LlmProfileName) => {
    if (!data || p === data.active || change.isPending) return;
    change.reset();
    if (p === "paid") setConfirm(true);
    else change.mutate(p);
  };
  const paid = data?.profiles.paid;
  const interval = sys?.agent_interval_minutes ?? 0;
  const now = data?.profiles[data.active];

  return (
    <div className="llm-us" data-testid="us-llm">
      <div className="llm-us-head">
        <b>🇺🇸 미장 매니저 LLM</b>
        {data && (
          <div className="seg llm-us-seg" role="group" aria-label="미장 매니저 LLM">
            {PROFILE_ORDER.map((key) => {
              const p = data.profiles[key];
              const on = data.active === key;
              return (
                <button
                  key={key}
                  type="button"
                  className={on ? `active ${key}` : key}
                  aria-pressed={on}
                  disabled={!p.configured || change.isPending}
                  title={p.configured ? (p.model ?? "") : `미설정 — .env 의 ${p.env} 를 채우세요`}
                  onClick={() => pick(key)}
                >
                  {p.label || PROFILE_KO[key]}
                </button>
              );
            })}
          </div>
        )}
      </div>
      {q.isLoading && <small className="muted">불러오는 중…</small>}
      {q.isError && <p className="error">{errorText(q.error)}</p>}
      {now && (
        <small className="muted">
          {now.model ?? "-"}
          {apiLabel(now.api) ? ` · ${apiLabel(now.api)}` : ""} — 미장 판단·답장에만 쓰여요
        </small>
      )}
      <div className="llm-status" aria-live="polite">
        {change.isPending && !confirm && <p className="muted">바꾸는 중…</p>}
        {change.isError && !confirm && <p className="error">{switchErrorText(change.error)}</p>}
        {data?.active === "paid" && (
          <p className="push-note warn">미장 매니저가 유료 모델을 쓰는 중이에요 — 호출마다 요금이 나가요.</p>
        )}
      </div>
      {confirm && paid && (
        <Modal
          title="미장 매니저를 유료 모델로 바꿀까요?"
          tone="danger"
          onClose={() => !change.isPending && setConfirm(false)}
          actions={
            <>
              <button className="btn ghost" onClick={() => setConfirm(false)} disabled={change.isPending}>
                취소
              </button>
              <button
                className="btn primary"
                onClick={() => change.mutate("paid")}
                disabled={change.isPending}
              >
                {change.isPending ? "바꾸는 중…" : "유료로 바꾸기"}
              </button>
            </>
          }
        >
          <p>
            유료 모델은 호출마다 실제 요금이 나가요.{" "}
            {interval > 0
              ? `지금 미장 일정(장중 ${interval}분마다 판단)이면 판단 세션과 채팅 답장마다 호출돼요.`
              : "미장 판단 세션과 채팅 답장마다 호출돼요."}
          </p>
          <p className="muted">
            {paid.model ?? "-"}
            {apiLabel(paid.api) ? ` · ${apiLabel(paid.api)}` : ""} — 국장 매니저 설정은 그대로이고, 언제든
            다시 무료로 돌릴 수 있어요.
          </p>
          <p>
            <Link to="/costs">LLM 요금 보기 →</Link>
          </p>
          {change.isError && <p className="error">{switchErrorText(change.error)}</p>}
        </Modal>
      )}
    </div>
  );
}

const TEST_FIELDS: LlmTestField[] = ["base_url", "model", "api", "reasoning_effort"];

const pickTestValues = (d: LlmTestValues): LlmTestValues => ({
  base_url: d.base_url,
  model: d.model,
  api: d.api,
  reasoning_effort: d.reasoning_effort,
});

/** 값이 어디서 왔는지 (화면에서 지정 / .env) */
function SourceBadge({ src }: { src: LlmTestSource | undefined }) {
  const screen = src === "화면";
  return <span className={`pill llm-src ${screen ? "screen" : ""}`}>{testSourceText(src)}</span>;
}

/**
 * 테스트용(무료) LLM 주소·모델·호출 방식·추론 강도를 화면에서 지정 (/api/llm/test-endpoint).
 * 키는 .env 의 LLM_TEST_API_KEY 에만 두고 화면에서 받지 않는다. 주소(내부 IP 일 수 있음)는 이 폼 안에서만 보여 준다.
 */
export function TestEndpointSettings() {
  const qc = useQueryClient();
  const refresh = useRefreshLlmViews();
  const ids = useId();
  const id = (k: string) => `${ids}-${k}`;
  const q = useQuery(testEndpointQuery);
  // 사용자가 고친 칸만 담는다 — 저장·되돌리기 뒤에는 비워서 서버 값을 그대로 보여 준다
  const [draft, setDraft] = useState<Partial<LlmTestValues>>({});

  const models = useMutation({
    mutationFn: (baseUrl: string) =>
      post<{ models: string[] }>(
        baseUrl ? `${TEST_URL}/models?base_url=${encodeURIComponent(baseUrl)}` : `${TEST_URL}/models`,
      ),
  });
  const check = useMutation({ mutationFn: () => post<LlmTestCheck>(`${TEST_URL}/check`) });

  const applied = (data: LlmTestEndpoint) => {
    qc.setQueryData(["llm-test-endpoint"], data);
    setDraft({});
    check.reset();
    refresh();
  };
  const save = useMutation({
    mutationFn: (body: LlmTestValues) => put<LlmTestEndpoint>(TEST_URL, body),
    onSuccess: (data) => {
      reset.reset();
      applied(data);
      toast("저장했어요 ✓ 무료 모델을 쓸 때 다음 판단·답장부터 적용돼요.");
    },
  });
  const reset = useMutation({
    mutationFn: () => del<LlmTestEndpoint>(TEST_URL),
    onSuccess: (data) => {
      save.reset();
      applied(data);
      toast(".env 값으로 되돌렸어요 ✓");
    },
  });

  // 예전 서버(이 기능이 없음)면 칸 자체를 그리지 않는다
  if (q.error instanceof ApiError && q.error.status === 404) return null;

  const data = q.data;
  const v: LlmTestValues | null = data ? { ...pickTestValues(data), ...draft } : null;
  const dirty = !!data && !!v && TEST_FIELDS.some((k) => v[k] !== data[k]);
  const fromScreen = !!data && TEST_FIELDS.some((k) => data.source[k] === "화면");
  const busy = save.isPending || reset.isPending;
  const edit = (patch: Partial<LlmTestValues>) => {
    save.reset();
    reset.reset();
    setDraft((d) => ({ ...d, ...patch }));
  };
  const envHint = (k: LlmTestField) =>
    data && data.source[k] === "화면" ? (
      <small className="muted">.env 값: {data.env[k] || "(비어 있음)"}</small>
    ) : null;
  const modelList = models.data?.models ?? [];

  return (
    <div className="llm-test" role="group" aria-label="테스트용(무료) LLM">
      <h3>테스트용(무료) LLM</h3>
      <p className="muted llm-test-help">
        여기서 바꾼 값은 앱에 저장돼 재시작해도 유지돼요. 키는 화면에서 받지 않아요.
      </p>
      {q.isLoading && <p className="muted">불러오는 중…</p>}
      {q.isError && <p className="error">{errorText(q.error)}</p>}
      {data && v && (
        <form
          className="form llm-test-form"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (dirty && !busy) save.mutate(v);
          }}
        >
          <div className="llm-test-field">
            <div className="llm-test-label">
              <label htmlFor={id("base_url")}>주소</label>
              <SourceBadge src={data.source.base_url} />
            </div>
            <input
              id={id("base_url")}
              type="url"
              inputMode="url"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              placeholder="http://서버:8000/v1"
              value={v.base_url}
              onChange={(e) => edit({ base_url: e.target.value })}
            />
            {envHint("base_url")}
          </div>

          <div className="llm-test-field">
            <div className="llm-test-label">
              <label htmlFor={id("model")}>모델</label>
              <SourceBadge src={data.source.model} />
            </div>
            <div className="llm-test-inline">
              <input
                id={id("model")}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                placeholder="모델 이름"
                value={v.model}
                onChange={(e) => edit({ model: e.target.value })}
              />
              <button
                type="button"
                className="btn tiny"
                disabled={models.isPending || !v.base_url.trim()}
                onClick={() => models.mutate(v.base_url.trim())}
              >
                {models.isPending ? "불러오는 중…" : "목록 불러오기"}
              </button>
            </div>
            {models.isSuccess &&
              (modelList.length > 0 ? (
                <select
                  aria-label="불러온 모델"
                  value={modelList.includes(v.model) ? v.model : ""}
                  onChange={(e) => e.target.value && edit({ model: e.target.value })}
                >
                  <option value="">불러온 모델 {modelList.length}개 중에서 고르기</option>
                  {modelList.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              ) : (
                <small className="muted">서버가 알려 준 모델이 없어요. 이름을 직접 적어 주세요.</small>
              ))}
            {models.isError && <small className="error">{errorText(models.error)}</small>}
            {envHint("model")}
          </div>

          <div className="llm-test-row">
            <div className="llm-test-field">
              <div className="llm-test-label">
                <label htmlFor={id("api")}>호출 방식</label>
                <SourceBadge src={data.source.api} />
              </div>
              <select
                id={id("api")}
                value={v.api}
                onChange={(e) => edit({ api: e.target.value as LlmTestApi })}
              >
                {TEST_API_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              {envHint("api")}
            </div>
            <div className="llm-test-field">
              <div className="llm-test-label">
                <label htmlFor={id("effort")}>추론 강도</label>
                <SourceBadge src={data.source.reasoning_effort} />
              </div>
              <select
                id={id("effort")}
                value={v.reasoning_effort}
                onChange={(e) => edit({ reasoning_effort: e.target.value })}
              >
                {REASONING_EFFORTS.some((o) => o.value === v.reasoning_effort) ? null : (
                  <option value={v.reasoning_effort}>{v.reasoning_effort}</option>
                )}
                {REASONING_EFFORTS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              {envHint("reasoning_effort")}
            </div>
          </div>

          <p className={`llm-test-key ${data.has_key ? "" : "muted"}`}>
            {data.has_key
              ? "키: .env 의 LLM_TEST_API_KEY 사용"
              : "키 없음 — 키를 받지 않는 서버면 그대로 쓸 수 있어요 (키는 .env 에만 넣어요)"}
          </p>

          <div className="btn-row">
            <button
              type="submit"
              className="btn primary"
              disabled={!dirty || busy || !v.base_url.trim() || !v.model.trim()}
            >
              {save.isPending ? "저장하는 중…" : "저장"}
            </button>
            {fromScreen && (
              <button type="button" className="btn ghost" disabled={busy} onClick={() => reset.mutate()}>
                {reset.isPending ? "되돌리는 중…" : ".env 값으로 되돌리기"}
              </button>
            )}
            <button
              type="button"
              className="btn"
              disabled={check.isPending || !data.configured}
              onClick={() => check.mutate()}
            >
              연결 확인 (무료)
            </button>
          </div>
          {dirty && data.configured && (
            <small className="muted">연결 확인은 저장된 값으로 해요 — 바꾼 값은 저장한 뒤 확인하세요.</small>
          )}

          <div className="llm-status" aria-live="polite">
            {save.isError && <p className="error">{switchErrorText(save.error)}</p>}
            {reset.isError && <p className="error">{switchErrorText(reset.error)}</p>}
            {check.isPending && (
              <p className="muted llm-checking">
                <span className="spinner" aria-hidden /> 무료 엔드포인트에 짧은 요청 4개를 보내는 중… (1분
                넘게 걸릴 수 있어요)
              </p>
            )}
            {check.isError && <p className="error">{errorText(check.error)}</p>}
          </div>
          {check.isSuccess && <CheckResult r={check.data} />}
        </form>
      )}
    </div>
  );
}

function CheckResult({ r }: { r: LlmTestCheck }) {
  return (
    <div className="llm-check" aria-label="연결 확인 결과">
      <p className={r.ok ? "ok" : "error"}>
        {r.ok ? "연결 확인 완료 ✓" : "일부 확인에 실패했어요"}{" "}
        <small className="muted">
          ({r.model} · {apiLabel(r.api as LlmProfileInfo["api"]) ?? r.api})
        </small>
      </p>
      <ul className="llm-check-steps">
        {r.steps.map((s, i) => (
          <li key={`${i}:${s.label}`} className={s.ok ? "ok" : "fail"}>
            <span>
              {s.ok ? "✅" : "❌"} {s.label}
            </span>
            {s.detail && <code>{s.detail}</code>}
          </li>
        ))}
      </ul>
    </div>
  );
}

const PROVIDER_ROWS: { key: "openai" | "openrouter" | "google" | "qwen"; label: string; env: string }[] = [
  { key: "openai", label: "OpenAI", env: "OPENAI_API_KEY" },
  { key: "openrouter", label: "OpenRouter", env: "OPENROUTER_API_KEY" },
  { key: "qwen", label: "Qwen Cloud", env: "QWEN_API_KEY" },
  { key: "google", label: "Google", env: "LLM_API_KEY (Gemini)" },
];

/** 고르기 값: 같은 이름이 제공사마다 있을 수 있어 "제공사|모델" 로 */
const optionValue = (m: Pick<LlmModelOption, "provider" | "id">) => `${m.provider}|${m.id}`;

/**
 * 유료 모델 고르기 (D-45, D-71): 키를 넣어 둔 제공사(OpenAI·OpenRouter·Google)의 모델을 한 목록에서 고른다.
 * OpenRouter 는 아래 '모델 등록'에서 등록한 것만 나온다. 바꿀 때 한 번 더 확인.
 */
function PaidModelPicker({
  paid,
  active,
  busy,
}: {
  paid: LlmPaidProfileInfo;
  active: LlmProfileName;
  busy: boolean;
}) {
  const apply = useApplyLlmProfile();
  const qc = useQueryClient();
  const selectId = useId();
  const [pick, setPick] = useState<LlmModelOption | null>(null);
  const change = useMutation({
    mutationFn: (m: LlmModelOption) =>
      put<LlmProfiles>("/api/llm/model", { model: m.id, provider: m.provider === "env" ? null : m.provider }),
    onSuccess: (data) => {
      setPick(null);
      apply(data);
      const p = data.profiles.paid;
      const name = p.models?.find((m) => m.selected)?.name ?? p.model ?? "-";
      toast(`유료 모델을 바꿨어요 ✓ (${name}) 다음 판단·답장부터 적용돼요.`);
    },
  });
  // OpenRouter 목록(등록 모델 가격)을 받아 오기 — 목록이 없거나 오래됐으면 화면을 열 때 한 번만
  const reload = useMutation({
    mutationFn: (force: boolean) =>
      post<LlmProfiles>(`/api/llm/openrouter/models/refresh?force=${force ? "true" : "false"}`),
    onSuccess: (data) => qc.setQueryData(["llm-profile"], data),
  });
  const hasOpenRouter = !!paid.providers?.openrouter;
  const needsLoad = hasOpenRouter && (!paid.models_loaded || !!paid.models_stale);
  const autoTried = useRef(false);
  const { mutate: reloadMutate } = reload;
  useEffect(() => {
    if (!needsLoad || autoTried.current) return;
    autoTried.current = true;
    reloadMutate(false);
  }, [needsLoad, reloadMutate]);

  const models = paid.models ?? [];
  if (!paid.configured && models.every((m) => !m.available)) return null;

  const current = models.find((m) => m.selected) ?? null;
  const groups = groupModels(models);
  const keys = paid.providers ?? {};
  const loading = reload.isPending;

  const onSelect = (value: string) => {
    const m = models.find((x) => optionValue(x) === value);
    if (!m || !m.available || m.selected || change.isPending) return;
    change.reset();
    setPick(m);
  };
  const cancel = () => !change.isPending && setPick(null);
  const pickPrice = pick ? modelPriceText(pick) : null;
  const providerOf = (m: LlmModelOption | null) =>
    m ? (MODEL_GROUPS.find((g) => g.key === m.provider)?.label ?? m.provider) : null;

  return (
    <div className="llm-model">
      <ul className="llm-keys" aria-label="제공사 키">
        {PROVIDER_ROWS.map((r) => (
          <li key={r.key} className={keys[r.key] ? "on" : undefined}>
            <b>{r.label}</b>{" "}
            <small className="muted">{keys[r.key] ? "키 있음" : `키 없음 · .env ${r.env}`}</small>
          </li>
        ))}
      </ul>
      <label htmlFor={selectId}>유료 모델</label>
      {models.length > 0 ? (
        <select
          id={selectId}
          value={pick ? optionValue(pick) : current ? optionValue(current) : ""}
          disabled={busy || change.isPending}
          onChange={(e) => onSelect(e.target.value)}
        >
          {!current && <option value="">고르세요</option>}
          {groups.map((g) => (
            <optgroup key={g.key} label={g.label}>
              {g.items.map((m) => (
                <option key={optionValue(m)} value={optionValue(m)} disabled={!m.available}>
                  {modelOptionText(m)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      ) : (
        <small className="muted">고를 수 있는 모델이 아직 없어요.</small>
      )}
      <small className="muted">
        {current ? `지금: ${providerOf(current)} · ${current.name}. ` : ""}요금은 100만 토큰당 달러예요.
        {paid.env_model ? ` .env 기본값: ${paid.env_model}` : ""}
        {current?.note ? ` · ${current.note}` : ""}
      </small>
      {hasOpenRouter && (
        <div className="llm-model-list">
          <span className="muted">
            {loading
              ? "OpenRouter 목록 불러오는 중…"
              : paid.models_fetched_at
                ? `OpenRouter 목록 갱신: ${fmtDateTime(paid.models_fetched_at)}`
                : "OpenRouter 목록을 아직 받지 않았어요"}
          </span>
          <button
            type="button"
            className="btn ghost"
            onClick={() => {
              reload.reset();
              reload.mutate(true);
            }}
            disabled={loading}
          >
            목록 새로고침
          </button>
        </div>
      )}
      {hasOpenRouter && !loading && (reload.isError || paid.models_error) && (
        <p className="error llm-model-error">
          OpenRouter 목록을 받지 못했어요: {reload.isError ? errorText(reload.error) : paid.models_error}
        </p>
      )}
      <div className="llm-status" aria-live="polite">
        {change.isError && !pick && <p className="error">{switchErrorText(change.error)}</p>}
      </div>
      {hasOpenRouter && (
        <OpenRouterRegistry
          registered={paid.openrouter_registered ?? []}
          models={models}
          envModel={paid.provider === "openrouter" ? (paid.env_model ?? null) : null}
        />
      )}
      {pick && (
        <Modal
          title="유료 모델을 바꿀까요?"
          tone="danger"
          onClose={cancel}
          actions={
            <>
              <button className="btn ghost" onClick={cancel} disabled={change.isPending}>
                취소
              </button>
              <button className="btn primary" onClick={() => change.mutate(pick)} disabled={change.isPending}>
                {change.isPending ? "바꾸는 중…" : "이 모델로 바꾸기"}
              </button>
            </>
          }
        >
          <p>유료 모델은 호출마다 실제 요금이 나가요. 비싼 모델일수록 판단·답장마다 요금이 커져요.</p>
          <p>
            <b>
              {providerOf(pick)} · {pick.name}
            </b>
            {pick.tier ? ` · ${pick.tier}` : ""}
            <br />
            <span className="muted">
              {pickPrice ? `${pickPrice} (100만 토큰당)` : "요금 정보가 없어요"}
              {current ? ` — 지금: ${providerOf(current)} · ${current.name}` : ""}
            </span>
          </p>
          <p>
            다음 판단·답장부터 적용돼요.
            {active === "free" ? " 지금은 무료를 쓰는 중이라 유료로 바꿨을 때 이 모델을 써요." : ""}
          </p>
          <p>
            <Link to="/costs">LLM 요금 보기 →</Link>
          </p>
          {change.isError && <p className="error">{switchErrorText(change.error)}</p>}
        </Modal>
      )}
    </div>
  );
}
