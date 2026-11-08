import type { LlmCostSource, LlmCosts, LlmLedgerGroup, LlmProvider } from "../api/types";

/** 직접 연결 단가표 제공사 (가격표 링크) — Anthropic 은 쓰지 않는다 (D-66) */
export const PROVIDERS: { id: LlmProvider | "openrouter"; label: string }[] = [
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
  { id: "openrouter", label: "OpenRouter" },
  { id: "qwen", label: "Qwen Cloud" },
];

const PROVIDER_KO: Record<string, string> = {
  openai: "OpenAI",
  google: "Google",
  openrouter: "OpenRouter",
  qwen: "Qwen Cloud",
  local: "자체 서버",
  gateway: "기타 유료 주소",
  "": "모름",
};
export const providerLabel = (p: string) => PROVIDER_KO[p] ?? p;

const PROFILE_KO: Record<string, string> = { free: "무료", paid: "유료", "": "모름" };
export const profileLabel = (p: string) => PROFILE_KO[p] ?? p;

const MARKET_KO: Record<string, string> = { KR: "🇰🇷 국장", US: "🇺🇸 미장", "": "공용" };
export const marketLabel = (m: string) => MARKET_KO[m] ?? m;

const KIND_KO: Record<string, string> = {
  session: "판단 세션",
  chat: "답장",
  newsroom: "뉴스 요약",
  memory: "기억 정리",
};
export const kindLabel = (k: string) => KIND_KO[k] ?? k;

export const SOURCE_KO: Record<LlmCostSource, string> = {
  reported: "제공사 보고",
  priced: "공식 단가 계산",
  free: "무료",
  unpriced: "단가 모름",
  estimated: "예전 기록 추정",
};
export const SOURCE_ORDER: LlmCostSource[] = ["reported", "priced", "free", "estimated", "unpriced"];

/** 계좌 이름: 주 계좌 · 실험실 전략 이름 (전략 목록에 없으면 id) */
export function strategyLabel(id: string, strategies: LlmCosts["strategies"]): string {
  if (!id) return "주 계좌";
  const s = strategies[id];
  return s ? `${s.emoji} ${s.name}` : id;
}

/** 시장 + 계좌 한 줄 (공용은 뉴스 룸 같은 시장과 관계없는 호출) */
export function accountLabel(market: string, strategy: string, strategies: LlmCosts["strategies"]): string {
  if (!market && !strategy) return "공용 (뉴스 룸)";
  return `${marketLabel(market)} · ${strategyLabel(strategy, strategies)}`;
}

// ---------- 거르기 ----------
export type FilterKey = "profile" | "provider" | "model" | "market" | "strategy" | "kind";
export const FILTER_KEYS: FilterKey[] = ["profile", "provider", "model", "market", "strategy", "kind"];
/** 빈 값("")은 전체 — 그 밖의 값은 정확히 같은 것만 */
export type Filters = Record<FilterKey, string | null>;
export const NO_FILTER: Filters = {
  profile: null,
  provider: null,
  model: null,
  market: null,
  strategy: null,
  kind: null,
};

export const matches = (g: LlmLedgerGroup, f: Filters) =>
  FILTER_KEYS.every((k) => f[k] == null || g[k] === f[k]);

export const isFiltered = (f: Filters) => FILTER_KEYS.some((k) => f[k] != null);

/** 데이터에 실제로 있는 값만 (거르기 목록) — 다른 거르기에 맞는 묶음 안에서 */
export function optionValues(groups: LlmLedgerGroup[], key: FilterKey, f: Filters): string[] {
  const others = { ...f, [key]: null };
  const seen = new Set<string>();
  for (const g of groups) if (matches(g, others)) seen.add(g[key]);
  return [...seen].sort((a, b) => a.localeCompare(b, "ko"));
}

// ---------- 합치기 ----------
export interface Totals {
  calls: number;
  input: number;
  cache_read: number;
  cache_known: number;
  output: number;
  reasoning: number;
  cost: number;
  unpriced: number;
  by_source: Partial<Record<LlmCostSource, number>>;
}

export const emptyTotals = (): Totals => ({
  calls: 0,
  input: 0,
  cache_read: 0,
  cache_known: 0,
  output: 0,
  reasoning: 0,
  cost: 0,
  unpriced: 0,
  by_source: {},
});

export function addTo(t: Totals, g: LlmLedgerGroup): Totals {
  t.calls += g.calls;
  t.input += g.input;
  t.cache_read += g.cache_read;
  t.cache_known += g.cache_known;
  t.output += g.output;
  t.reasoning += g.reasoning;
  t.cost += g.cost;
  t.unpriced += g.unpriced;
  for (const [k, v] of Object.entries(g.by_source) as [LlmCostSource, number][]) {
    t.by_source[k] = (t.by_source[k] ?? 0) + v;
  }
  return t;
}

export function sum(groups: LlmLedgerGroup[], f: Filters = NO_FILTER): Totals {
  const t = emptyTotals();
  for (const g of groups) if (matches(g, f)) addTo(t, g);
  return t;
}

/** 묶음 키 → 합계 (나온 순서) */
export function groupBy<G extends LlmLedgerGroup>(
  groups: G[],
  key: (g: G) => string,
  f: Filters = NO_FILTER,
): Map<string, Totals> {
  const out = new Map<string, Totals>();
  for (const g of groups) {
    if (!matches(g, f)) continue;
    const k = key(g);
    addTo(out.get(k) ?? out.set(k, emptyTotals()).get(k)!, g);
  }
  return out;
}

/** 한 달 막대를 쌓는 조각: 캐시 읽기 · 입력 나머지 · 출력 */
export function tokenStack(t: Pick<Totals, "input" | "cache_read" | "output">) {
  const input = Math.max(0, t.input);
  const cached = Math.min(Math.max(0, t.cache_read), input);
  const output = Math.max(0, t.output);
  return { cached, rest: input - cached, output, total: input + output };
}

/** 고른 달의 날짜들 (1일 → 말일, 이번 달이면 오늘까지) */
export function daysOf(month: string, today: string): string[] {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const out: string[] = [];
  for (let d = 1; d <= last; d++) {
    const day = `${month}-${String(d).padStart(2, "0")}`;
    if (day > today) break;
    out.push(day);
  }
  return out;
}

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

/** "2026-11-05" → "11월 5일 (목)" */
export function dayLong(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const dow = "일월화수목금토"[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${m}월 ${d}일 (${dow})`;
}

/** 링크로 걸어도 되는 주소만 (http/https) */
export const safeUrl = (u: string | undefined) => (u && /^https?:\/\//i.test(u) ? u : null);
