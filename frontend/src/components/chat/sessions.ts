// 트레이딩 룸: 판단 세션(run_id) 단위로 채팅 메시지를 묶고 한눈에 볼 요약을 만든다 (화면과 분리된 순수 함수)
import type { AgentRun, ChatMsg } from "../../api/types";

export const TRIGGER_KO: Record<string, string> = {
  morning: "장전",
  hourly: "정시",
  afternoon: "오후",
  manual: "수동",
};

export const SOURCE_CLS: Record<string, string> = {
  토스증권: "toss",
  DART: "dart",
  NAVER: "naver",
  모의계좌: "account",
  "토스 실계좌": "account",
};

export type FeedItem = { type: "msg"; msg: ChatMsg } | { type: "session"; runId: number; msgs: ChatMsg[] };

/** 세션에 속한 메시지(run_id 있음, 사용자 메시지 제외)는 처음 나온 자리에 한 덩어리로 모은다. 나머지는 그대로 */
export function groupFeed(messages: ChatMsg[]): FeedItem[] {
  const items: FeedItem[] = [];
  const byRun = new Map<number, ChatMsg[]>();
  for (const m of messages) {
    if (m.run_id == null || m.role === "user") {
      items.push({ type: "msg", msg: m });
      continue;
    }
    let group = byRun.get(m.run_id);
    if (!group) {
      group = [];
      byRun.set(m.run_id, group);
      items.push({ type: "session", runId: m.run_id, msgs: group });
    }
    group.push(m);
  }
  return items;
}

const sameRefs = <T>(a: readonly T[], b: readonly T[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * 새로 묶은 목록에서 내용이 그대로인 세션 묶음은 지난번 것을 그대로 쓴다 (같은 배열 → 세션 요약·블록을 다시 만들지 않는다).
 * 새 메시지가 붙은 세션만 새 배열이 된다
 */
export function stabilizeGroups(prev: FeedItem[], next: FeedItem[]): FeedItem[] {
  if (prev.length === 0) return next;
  const old = new Map<number, Extract<FeedItem, { type: "session" }>>();
  for (const it of prev) if (it.type === "session") old.set(it.runId, it);
  return next.map((it) => {
    if (it.type !== "session") return it;
    const p = old.get(it.runId);
    return p && sameRefs(p.msgs, it.msgs) ? p : it;
  });
}

// ------------------------------------------------------------ 확인한 정보 분류
export type CategoryKey = "account" | "market" | "flow" | "research" | "news" | "rule" | "other";

export const CATEGORIES: { key: CategoryKey; label: string; icon: string }[] = [
  { key: "account", label: "계좌", icon: "💼" },
  { key: "market", label: "시세·차트", icon: "📈" },
  { key: "flow", label: "수급", icon: "🧲" },
  { key: "research", label: "재무·공시", icon: "🧾" },
  { key: "news", label: "뉴스", icon: "📰" },
  { key: "rule", label: "규칙 점수", icon: "🧮" },
  { key: "other", label: "기타", icon: "🔧" },
];

const TOOL_CATEGORY: Record<string, CategoryKey> = {
  get_portfolio: "account",
  get_trade_history: "account",
  screen_stocks: "market",
  get_price_history: "market",
  get_market_overview: "market",
  explore_sector: "market",
  get_stock_status: "market",
  get_supply_demand: "flow",
  get_financials: "research",
  get_disclosures: "research",
  search_news: "news",
  rule_score: "rule",
};

/** 도구 이름으로 분류하고, 이름이 없는 예전 메시지는 라벨 글자로 짐작한다 */
export function toolCategory(m: ChatMsg): CategoryKey {
  const tool = typeof m.payload.tool === "string" ? m.payload.tool : "";
  if (TOOL_CATEGORY[tool]) return TOOL_CATEGORY[tool];
  const c = m.content;
  if (c.includes("뉴스")) return "news";
  if (c.includes("재무") || c.includes("공시")) return "research";
  if (c.includes("수급")) return "flow";
  if (c.includes("포트폴리오") || c.includes("매매 내역")) return "account";
  if (/차트|스크리닝|시장|섹터|상태 점검/.test(c)) return "market";
  if (c.includes("점수")) return "rule";
  return "other";
}

export interface ToolGroup {
  key: CategoryKey;
  label: string;
  icon: string;
  msgs: ChatMsg[];
  sources: [string, number][];
  failed: number;
}

const isFailedTool = (m: ChatMsg) => {
  const preview = m.payload.preview as Record<string, unknown> | undefined;
  return m.payload.ok === false || preview?.error != null;
};

function countSources(msgs: ChatMsg[]): [string, number][] {
  const out = new Map<string, number>();
  for (const m of msgs) {
    const s = m.payload.source;
    if (typeof s === "string" && s) out.set(s, (out.get(s) ?? 0) + 1);
  }
  return [...out.entries()].sort((a, b) => b[1] - a[1]);
}

export function groupTools(tools: ChatMsg[]): ToolGroup[] {
  return CATEGORIES.map((c) => {
    const msgs = tools.filter((m) => toolCategory(m) === c.key);
    return { ...c, msgs, sources: countSources(msgs), failed: msgs.filter(isFailedTool).length };
  }).filter((g) => g.msgs.length > 0);
}

// ------------------------------------------------------------ 세션 요약
export type SessionStatus = "running" | "done" | "failed" | "ended";

export const STATUS_LABEL: Record<SessionStatus, string> = {
  running: "진행 중",
  done: "완료",
  failed: "실패",
  ended: "중단됨",
};

export type RunInfo = Partial<
  Pick<AgentRun, "status" | "trigger" | "started_at" | "finished_at" | "summary" | "market_view" | "error">
>;

export interface SessionView {
  runId: number;
  trigger: string | null;
  startedAt: string | null;
  durationMs: number | null;
  status: SessionStatus;
  error: string | null;
  marketView: string | null;
  summary: string | null;
  counts: Record<string, number> | null;
  decisions: ChatMsg[];
  risk: ChatMsg[];
  tools: ChatMsg[];
  notes: ChatMsg[]; // 매니저 생각·시스템 안내 (진행 과정)
  sources: [string, number][];
}

const FAIL_RE = /^⚠️\s*세션\s*#\d+\s*중 문제가 생겼어요:\s*/;
const FAIL_TAIL = /\.?\s*이번 세션 주문은 내지 않았어요\.?\s*$/;
const START_RE = /출근했어요/;
const MARKET_RE = /^🌏\s*시장 관점:\s*/;

const ms = (iso: string | null | undefined) => (iso ? new Date(iso).getTime() : NaN);

/**
 * live: 서버 진행 상태로 본 실행 여부 — true 진행 중, false 진행 중 아님(서버 기록의 RUNNING 은 끊긴 세션),
 * undefined 아직 모름(기록·메시지로만 판단)
 */
export function summarizeSession(runId: number, msgs: ChatMsg[], run?: RunInfo, live?: boolean): SessionView {
  let trigger: string | null = run?.trigger ?? null;
  let failMsg: string | null = null;
  let marketView: string | null = null;
  let summaryMsg: ChatMsg | null = null;
  const decisions: ChatMsg[] = [];
  const risk: ChatMsg[] = [];
  const tools: ChatMsg[] = [];
  const notes: ChatMsg[] = [];

  for (const m of msgs) {
    if (m.kind === "tool") tools.push(m);
    else if (m.kind === "decision") decisions.push(m);
    else if (m.kind === "summary") summaryMsg = m;
    else if (m.role === "risk") risk.push(m);
    else if (m.role === "system" && FAIL_RE.test(m.content)) {
      failMsg = m.content.replace(FAIL_RE, "").replace(FAIL_TAIL, "");
    } else if (m.role === "system" && (typeof m.payload.trigger === "string" || START_RE.test(m.content))) {
      if (!trigger && typeof m.payload.trigger === "string") trigger = m.payload.trigger;
    } else if (m.role === "analyst" && m.kind === "text" && MARKET_RE.test(m.content)) {
      marketView = m.content.replace(MARKET_RE, "");
    } else notes.push(m);
  }

  const status: SessionStatus = live
    ? "running"
    : failMsg != null || run?.status === "FAILED"
      ? "failed"
      : run?.status === "DONE" || summaryMsg
        ? "done"
        : run?.status === "RUNNING" && live === undefined
          ? "running"
          : "ended";

  const first = msgs[0]?.ts ?? null;
  const startedAt = run?.started_at ?? first;
  let durationMs: number | null = null;
  if (status !== "running") {
    const end = run?.finished_at ?? (msgs.length ? msgs[msgs.length - 1].ts : null);
    const d = ms(end) - ms(startedAt);
    durationMs = Number.isFinite(d) && d >= 0 ? d : null;
  }

  const counts = (summaryMsg?.payload.counts as Record<string, number> | undefined) ?? null;
  return {
    runId,
    trigger,
    startedAt,
    durationMs,
    status,
    error: failMsg ?? run?.error ?? null,
    marketView: marketView ?? (run?.market_view || null),
    summary: summaryMsg?.content ?? (run?.summary || null),
    counts,
    decisions,
    risk,
    tools,
    notes,
    sources: countSources(tools),
  };
}

export function fmtDuration(msv: number | null): string | null {
  if (msv == null || !Number.isFinite(msv) || msv < 0) return null;
  const s = Math.round(msv / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m}분 ${s % 60}초` : `${m}분`;
  return `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

/** 서버 세션 상태(RUNNING/DONE/FAILED) → 화면 상태 */
export function runStatus(status: string | undefined): SessionStatus {
  return status === "RUNNING"
    ? "running"
    : status === "DONE"
      ? "done"
      : status === "FAILED"
        ? "failed"
        : "ended";
}

export function runDuration(run: RunInfo): string | null {
  if (!run.started_at || !run.finished_at) return null;
  return fmtDuration(ms(run.finished_at) - ms(run.started_at));
}

/**
 * 세션 요약 기억 — 메시지 묶음(같은 배열)·서버 기록(같은 객체)·진행 여부가 그대로면 지난 요약 객체를 그대로 준다.
 * 그래서 메모된 SessionBlock 이 진행 이벤트·다른 세션의 새 메시지마다 다시 그려지지 않는다
 */
export class SessionViewCache {
  private cache = new Map<number, { msgs: ChatMsg[]; run?: RunInfo; live?: boolean; view: SessionView }>();

  get(runId: number, msgs: ChatMsg[], run?: RunInfo, live?: boolean): SessionView {
    const hit = this.cache.get(runId);
    if (hit && hit.msgs === msgs && hit.run === run && hit.live === live) return hit.view;
    const view = summarizeSession(runId, msgs, run, live);
    if (this.cache.size > 200) this.cache.clear();
    this.cache.set(runId, { msgs, run, live, view });
    return view;
  }
}
