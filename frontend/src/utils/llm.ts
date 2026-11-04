import type { LlmModelOption, LlmProfileInfo, LlmProfileName, LlmTestApi, LlmTestSource } from "../api/types";

export const PROFILE_KO: Record<LlmProfileName, string> = { free: "무료", paid: "유료" };

const API_KO: Record<NonNullable<LlmProfileInfo["api"]>, string> = {
  chat: "Chat Completions",
  responses: "Responses API",
};

export const apiLabel = (api: LlmProfileInfo["api"]): string | null => (api ? API_KO[api] : null);

/** "유료 · 모델이름" — 예전 서버처럼 profile 이 없으면 모델 이름만 */
export function profileModel(profile: LlmProfileName | undefined, model: string | null): string {
  const name = profile ? PROFILE_KO[profile] : null;
  return name ? `${name} · ${model ?? "-"}` : (model ?? "-");
}

/** 상단 바 칩 글자 */
export function llmChipText(llm: { configured: boolean; model: string | null; profile?: LlmProfileName }) {
  return llm.configured ? profileModel(llm.profile, llm.model) : "LLM 미설정(규칙)";
}

/** 유료 모델 목록의 묶음 순서와 이름 */
export const MODEL_GROUPS: { key: LlmModelOption["provider"]; label: string }[] = [
  { key: "openai", label: "OpenAI" },
  { key: "google", label: "Google" },
  { key: "anthropic", label: "Anthropic" },
  { key: "openrouter", label: "OpenRouter" },
  { key: "env", label: ".env" },
];

export interface ModelGroup {
  key: string;
  label: string;
  items: LlmModelOption[];
}

/** 목록 묶기 — m.group(OpenRouter: 만든 곳) 이 없으면 m.provider. 순서는 MODEL_GROUPS → 그 밖(나온 순서) → .env */
export function groupModels(models: LlmModelOption[]): ModelGroup[] {
  const map = new Map<string, ModelGroup>();
  for (const m of models) {
    const key = m.group ?? m.provider;
    let g = map.get(key);
    if (!g) {
      const label = m.group_label || (MODEL_GROUPS.find((x) => x.key === key)?.label ?? key);
      g = { key, label, items: [] };
      map.set(key, g);
    }
    g.items.push(m);
  }
  const known = MODEL_GROUPS.map((g) => g.key as string).filter((k) => k !== "env");
  const rank = (k: string) => {
    if (k === "env") return known.length + 1;
    const i = known.indexOf(k);
    return i < 0 ? known.length : i;
  };
  return [...map.values()].sort((a, b) => rank(a.key) - rank(b.key));
}

/** 모델 검색 — id·이름·묶음으로 거른다. keep 에 든 id(지금·고른 모델)는 늘 남긴다 */
export function filterModels(models: LlmModelOption[], query: string, keep: string[] = []): LlmModelOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return models;
  return models.filter(
    (m) =>
      keep.includes(m.id) ||
      [m.id, m.name, m.group, m.group_label].some((v) => v != null && v.toLowerCase().includes(q)),
  );
}

/** 문맥 길이: 200000 → "200K", 131072 → "128K", 1048576 → "1M" */
export function contextText(n: number): string {
  const bin = n % 1024 === 0;
  const k = bin ? n / 1024 : n / 1000;
  if (k >= 1000) return `${Number((bin ? k / 1024 : k / 1000).toFixed(1))}M`;
  return `${Number(k.toFixed(k < 10 ? 1 : 0))}K`;
}

/** 100만 토큰당 달러 — 0.1 → "$0.1", 2 → "$2", 1.25 → "$1.25" */
export const fmtUsd = (n: number): string => `$${Number(n.toFixed(4))}`;

/** "입력 $2 / 출력 $10" — 요금을 모르면 null */
export function modelPriceText(m: Pick<LlmModelOption, "input" | "output">): string | null {
  if (m.input == null && m.output == null) return null;
  const f = (n: number | null) => (n == null ? "?" : fmtUsd(n));
  return `입력 ${f(m.input)} / 출력 ${f(m.output)}`;
}

/** 못 쓰는 이유를 목록에 붙일 짧은 말로 */
export function shortReason(reason: string | null): string {
  if (!reason) return "사용 불가";
  if (reason.includes("키")) return "키 필요";
  if (reason.includes("연결 전") || reason.includes("아직")) return "준비 중";
  return "사용 불가";
}

/**
 * 목록 한 줄: "GPT-6.1 Sol · 주력 · 입력 $2 / 출력 $10" (+ 못 쓰면 " — 키 필요").
 * OpenRouter: "Claude X · 입력 $3 / 출력 $15 · 무료 · 200K" — 요금을 모르면 "가격 정보 없음"
 */
export function modelOptionText(m: LlmModelOption): string {
  const parts: (string | null)[] = [m.name, m.tier];
  if (m.provider === "openrouter") {
    parts.push(
      modelPriceText(m) ?? "가격 정보 없음",
      (m.note ?? "").includes("무료") ? "무료" : null,
      m.context_length ? contextText(m.context_length) : null,
    );
  } else {
    parts.push(modelPriceText(m));
  }
  const text = parts.filter(Boolean).join(" · ");
  return m.available ? text : `${text} — ${shortReason(m.reason)}`;
}

/** 테스트용(무료) LLM 호출 방식 고르기 */
export const TEST_API_OPTIONS: { value: LlmTestApi; label: string }[] = [
  { value: "auto", label: "auto — api.openai.com 이면 Responses, 그 외 Chat" },
  { value: "chat", label: "chat — Chat Completions (/chat/completions)" },
  { value: "responses", label: "responses — Responses API (/responses)" },
];

/** 추론 강도 — "" 는 보내지 않음(모델 기본) */
export const REASONING_EFFORTS: { value: string; label: string }[] = [
  { value: "", label: "모델 기본 (보내지 않음)" },
  ...["none", "minimal", "low", "medium", "high", "xhigh", "max"].map((v) => ({ value: v, label: v })),
];

/** 출처 배지 글자: "화면" → "화면에서 지정" */
export const testSourceText = (src: LlmTestSource | undefined): string =>
  src === "화면" ? "화면에서 지정" : ".env";
