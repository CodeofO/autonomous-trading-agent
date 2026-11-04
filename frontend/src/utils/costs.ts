import type { LlmModelCost, LlmProvider, LlmScenario, LlmUsageMonth } from "../api/types";

export const PROVIDERS: { id: LlmProvider; label: string }[] = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
];

export const providerLabel = (p: LlmProvider) => PROVIDERS.find((x) => x.id === p)?.label ?? p;

/** 한 달 토큰을 쌓는 막대 조각: 입력(캐시 적중 추정) · 입력(나머지 = 입력 − 캐시) · 출력 */
export interface TokenStack {
  cached: number;
  rest: number;
  output: number;
  total: number;
}

export function tokenStack(m: Pick<LlmUsageMonth, "input" | "cached" | "output">): TokenStack {
  const input = Math.max(0, m.input || 0);
  // 캐시는 입력의 일부 — 서버 값이 어긋나도 입력보다 크게 그리지 않는다
  const cached = Math.min(Math.max(0, m.cached || 0), input);
  const rest = input - cached;
  const output = Math.max(0, m.output || 0);
  return { cached, rest, output, total: cached + rest + output };
}

/** 기록이 하나도 없는 달들인지 (모두 0) */
export const isEmptyUsage = (months: LlmUsageMonth[]) =>
  months.every((m) => !m.calls && !m.input && !m.output && !m.backfill);

/** 막대 아래 달 이름: 첫 달·1월은 연도까지 ("25.12"), 나머지는 "3월" */
export function monthLabel(month: string, index: number): string {
  const [y, m] = month.split("-");
  const n = Number(m);
  return index === 0 || n === 1 ? `${y.slice(2)}.${n}` : `${n}월`;
}

/** "2026-11" → "2026년 11월" */
export function monthLong(month: string): string {
  const [y, m] = month.split("-");
  return `${y}년 ${Number(m)}월`;
}

export type SortKey = "projection" | "spent" | "input" | "output" | "name";
export type SortDir = "asc" | "desc";

/** 이번 달 지금까지 쓴 금액 (USD) */
export const spentThisMonth = (m: LlmModelCost, month: string, scenario: LlmScenario) =>
  m.months[month]?.[scenario] ?? 0;

function sortValue(m: LlmModelCost, key: SortKey, month: string, scenario: LlmScenario): number | string {
  switch (key) {
    case "projection":
      return m.projection[scenario] ?? 0;
    case "spent":
      return spentThisMonth(m, month, scenario);
    case "input":
      return m.price.input;
    case "output":
      return m.price.output;
    case "name":
      return m.name;
  }
}

/** 비교표 정렬. 같은 값이면 원래 순서(회사·등급 순)를 지킨다 */
export function sortModels(
  models: LlmModelCost[],
  key: SortKey,
  dir: SortDir,
  month: string,
  scenario: LlmScenario,
): LlmModelCost[] {
  const sign = dir === "asc" ? 1 : -1;
  return models
    .map((m, i) => ({ m, i, v: sortValue(m, key, month, scenario) }))
    .sort((a, b) => {
      const c =
        typeof a.v === "string" || typeof b.v === "string"
          ? String(a.v).localeCompare(String(b.v), "ko")
          : a.v - b.v;
      return c !== 0 ? c * sign : a.i - b.i;
    })
    .map((x) => x.m);
}

/** 이번 달 예상이 가장 싼 모델·가장 비싼 모델. 모두 0 이면(기록 없음) 표시하지 않는다 */
export function extremes(
  models: LlmModelCost[],
  scenario: LlmScenario,
): { cheapest: string | null; priciest: string | null } {
  if (models.length < 2) return { cheapest: null, priciest: null };
  let lo = models[0];
  let hi = models[0];
  for (const m of models) {
    if (m.projection[scenario] < lo.projection[scenario]) lo = m;
    if (m.projection[scenario] > hi.projection[scenario]) hi = m;
  }
  if (hi.projection[scenario] <= 0 || lo.projection[scenario] === hi.projection[scenario])
    return { cheapest: null, priciest: null };
  return { cheapest: lo.id, priciest: hi.id };
}

/** 링크로 걸어도 되는 주소만 (http/https) */
export const safeUrl = (u: string | undefined) => (u && /^https?:\/\//i.test(u) ? u : null);
