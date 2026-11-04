import type { LlmModelOption, LlmProfileInfo, LlmProfileName } from "../api/types";

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
  { key: "env", label: ".env" },
];

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

/** 목록 한 줄: "GPT-6.1 Sol · 주력 · 입력 $2 / 출력 $10" (+ 못 쓰면 " — 키 필요") */
export function modelOptionText(m: LlmModelOption): string {
  const text = [m.name, m.tier, modelPriceText(m)].filter(Boolean).join(" · ");
  return m.available ? text : `${text} — ${shortReason(m.reason)}`;
}
