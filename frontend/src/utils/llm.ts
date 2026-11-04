import type { LlmProfileInfo, LlmProfileName } from "../api/types";

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
