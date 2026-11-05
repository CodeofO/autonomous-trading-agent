import { api, del, get, post, put } from "../api/client";
import type {
  MarketCode,
  MemoCard,
  MemoCardCreate,
  MemoCardPatch,
  MemoCards,
  MemoKind,
  MemoryTopic,
  MemoryTopics,
  MemoSettings,
  StockSearchItem,
  TopicPin,
} from "../api/types";

/** 시장별 API 접두어: 국장 "/api" · 미장 "/api/us" */
export const memoPrefix = (market: MarketCode) => (market === "US" ? "/api/us" : "/api");

/**
 * 메모 카드·종목 검색 API 주소를 한곳에 모은다.
 * 시장 접두어만 바꿔 쓴다 — 국장 memoApi("/api") · 미장 memoApi("/api/us") (메모 화면은 지금 시장 것을 쓴다)
 */
export function memoApi(prefix = "/api") {
  const paths = {
    cards: `${prefix}/memory/cards`,
    card: (id: number) => `${prefix}/memory/cards/${id}`,
    settings: `${prefix}/memory/settings`,
    topics: `${prefix}/memory/topics`,
    topic: (id: number) => `${prefix}/memory/topics/${id}`,
    review: `${prefix}/memory/topics/review`,
    search: (q: string, limit = 20) => `${prefix}/market/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  };
  return {
    paths,
    list: () => get<MemoCards>(paths.cards),
    create: (body: MemoCardCreate) => post<MemoCard>(paths.cards, body),
    update: (id: number, body: MemoCardPatch) =>
      api<MemoCard>(paths.card(id), { method: "PATCH", json: body }),
    remove: (id: number) => del<null>(paths.card(id)),
    saveSettings: (body: Pick<MemoSettings, "topics">) => put<MemoSettings>(paths.settings, body),
    topics: () => get<MemoryTopics>(paths.topics),
    pinTopic: (id: number, pin: TopicPin) =>
      api<MemoryTopic>(paths.topic(id), { method: "PATCH", json: { pin } }),
    removeTopic: (id: number) => del<null>(paths.topic(id)),
    reviewTopics: () => post<MemoryTopics>(paths.review, {}),
    search: (q: string, limit = 20) => get<StockSearchItem[]>(paths.search(q, limit)),
  };
}

export const MEMO_API = memoApi();

export interface MemoKindInfo {
  key: MemoKind;
  label: string;
  icon: string;
  /** 종목을 고르는 카드인지 (운용 지시는 종목 없음) */
  stock: boolean;
  hint: string;
}

export const MEMO_KINDS: MemoKindInfo[] = [
  {
    key: "watch",
    label: "관심 종목",
    icon: "👀",
    stock: true,
    hint: "계속 지켜봐요 · 시세 갱신 목록에도 들어가요",
  },
  { key: "focus", label: "주목할 종목", icon: "🎯", stock: true, hint: "적극적으로 매수를 검토해요" },
  { key: "avoid", label: "피할 종목", icon: "🚫", stock: true, hint: "사지 않아요" },
  { key: "note", label: "운용 지시", icon: "📝", stock: false, hint: "자유롭게 적는 지시 (종목 없음)" },
];

export const memoKind = (k: MemoKind): MemoKindInfo => MEMO_KINDS.find((m) => m.key === k) ?? MEMO_KINDS[3];

/** 판단·답장 때 실제로 기억에 들어가는 카드 (켜져 있고 만료 안 됨) */
export const isLive = (c: Pick<MemoCard, "active" | "expired">) => c.active && !c.expired;

/** 종류별로 묶는다. 묶음 안에서는 새 카드가 먼저 (켜고 끌 때 자리가 바뀌지 않게 켜짐 여부로는 정렬하지 않는다) */
export function groupCards(cards: MemoCard[]): Record<MemoKind, MemoCard[]> {
  const out: Record<MemoKind, MemoCard[]> = { watch: [], focus: [], avoid: [], note: [] };
  for (const c of [...cards].sort((a, b) => b.id - a.id)) (out[c.kind] ?? out.note).push(c);
  return out;
}

/** 검색 결과 한 줄: "삼성전자 · 005930 · KOSPI" */
export const stockOptionText = (s: StockSearchItem) =>
  [s.name, s.symbol, s.market].filter(Boolean).join(" · ");

/**
 * 종목을 목록에서 고르지 않았을 때 그대로 보낼 수 있는 종목코드.
 * 국장 6자리 (예: 005930, 0001A0) · 미장 티커 (예: AAPL, BRK.B — 영문으로 시작, 10자 이내)
 */
export const looksLikeCode = (q: string, market: MarketCode = "KR") =>
  market === "US"
    ? /^[A-Z][A-Z0-9.-]{0,9}$/.test(q.trim().toUpperCase())
    : /^[0-9A-Z]{6}$/.test(q.trim().toUpperCase());

// ---------- 날짜 (한국 날짜 "YYYY-MM-DD") ----------
const pad = (n: number) => String(n).padStart(2, "0");

/** 지금 한국 날짜 "YYYY-MM-DD" */
export function todayKst(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const v = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${v("year")}-${v("month")}-${v("day")}`;
}

function parseDay(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}
const isoDay = (d: Date) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;

export function addDays(day: string, n: number): string {
  const d = parseDay(day);
  d.setUTCDate(d.getUTCDate() + n);
  return isoDay(d);
}

/** 한 달 뒤 같은 날 (그달에 없는 날이면 그달 마지막 날: 01-31 → 02-28) */
export function addMonths(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1 + n, 1));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  first.setUTCDate(Math.min(d, last));
  return isoDay(first);
}

export type ExpiryPreset = "today" | "week" | "month" | "none";

export const EXPIRY_PRESETS: { key: ExpiryPreset; label: string }[] = [
  { key: "today", label: "오늘까지" },
  { key: "week", label: "1주" },
  { key: "month", label: "1달" },
  { key: "none", label: "없음" },
];

/** 빠른 만료일 버튼 → "YYYY-MM-DD" | null (없음) */
export function expiryFor(preset: ExpiryPreset, today: string): string | null {
  switch (preset) {
    case "today":
      return today;
    case "week":
      return addDays(today, 7);
    case "month":
      return addMonths(today, 1);
    default:
      return null;
  }
}

/** 지금 만료일이 어느 빠른 버튼과 같은지 (같은 게 없으면 null) */
export const presetOf = (expires: string | null, today: string): ExpiryPreset | null =>
  EXPIRY_PRESETS.find((p) => expiryFor(p.key, today) === expires)?.key ?? null;

/** 만료일이 지났는지 (그날까지는 유효) */
export const isExpiredOn = (expires: string | null, today: string) => !!expires && expires < today;

const WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

/** "2026-11-11" → "11/11(수)" */
export function fmtDay(day: string): string {
  const d = parseDay(day);
  if (Number.isNaN(d.getTime())) return day;
  return `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())}(${WEEKDAY[d.getUTCDay()]})`;
}

// ---------- 대화에서 자라는 기억 (D-67) ----------
/** 중요도 0~1 → 0~100 정수 (막대·글자) */
export const topicPercent = (score: number) => Math.round(Math.max(0, Math.min(1, score)) * 100);

/** 주제 상태 한 줄: 늘 기억 · 빠짐(직접) · 잊음 · 기억 중 · 빠짐 */
export function topicState(t: MemoryTopic): { label: string; tone: "on" | "off" | "pin" } {
  if (t.pin === "pin") return { label: "늘 기억", tone: "pin" };
  if (t.pin === "mute") return { label: "직접 뺌", tone: "off" };
  if (t.forgotten) return { label: "잊음", tone: "off" };
  return t.included ? { label: "기억 중", tone: "on" } : { label: "빠짐", tone: "off" };
}

/** 매니저 판단 표시 */
export const LLM_LEVEL_LABEL: Record<number, string> = { 1: "⬆ 올림", 0: "유지", [-1]: "⬇ 내림" };

/** "2026-11-04" → "11/04" */
export const shortDay = (d: string | null) => (d ? d.slice(5).replace("-", "/") : "-");
