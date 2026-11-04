import type { QueryClient } from "@tanstack/react-query";
import type { AgentProgress, AuditEvent, ChatMsg, MarketCode, Role } from "../api/types";
import { isMarketCode } from "../market";

/** 웹소켓 한 줄 (backend/app/engine/events.py EventHub, app/api/ws.py) */
export type WsMsg =
  | { channel: "chat"; data: ChatMsg }
  | { channel: "audit"; data: AuditEvent }
  | { channel: "typing"; data: { role: Role; active: boolean; label: string } }
  | { channel: "progress"; data: AgentProgress }
  | { channel: "state"; data: Record<string, unknown> }
  | { channel: "hello" | "pong"; data: unknown };

/**
 * 이벤트 하나로 다시 받아야 하는 캐시.
 * - market: 그 시장 캐시의 이름 (쿼리 키 [시장, 이름, …]). "*" 는 그 시장 캐시 전부
 * - global: 시장과 관계없는 캐시의 이름 (쿼리 키 [이름, …]). ["schedule", 시장] 처럼 둘째 칸이 시장이면 그 시장 것만
 */
export interface Targets {
  market: string[];
  global: string[];
}

const T = (market: string[], global: string[] = []): Targets => ({ market, global });

// 주문 접수·거부·취소: 주문 목록, 주문 가능 현금, 결정 기록의 '결과' 칸
const ORDER = T(["orders", "portfolio", "decisions"], ["overview"]);
// 체결: 보유·현금·자산 추이·체결 내역, 종목 차트의 체결 표시
const FILL = T(["orders", "fills", "portfolio", "equity", "decisions", "candles"], ["overview"]);
// 매니저 결정: 결정 기록, 종목 차트의 AI 결정 표시
const DECISION = T(["decisions", "candles"]);
// 세션 정리: 세션 목록(요약·상태), 결정, 매니저의 기억(구조 화면), 한눈에 보기의 마지막 세션
const SESSION_DONE = T(["runs", "decisions", "agent-structure"], ["overview"]);
// 마감 리포트 (시스템): 자산 추이·계좌
const DAILY_REPORT = T(["equity", "portfolio", "runs"], ["overview"]);
// 세션 시작·실패 안내 (시스템, run_id 있음)
const SESSION_NOTE = T(["runs"], ["overview"]);
// 그 밖의 시스템 안내 — 엔진 정지·재개, LLM·일정·데이터 소스 변경, 재시작처럼 드물고 여러 화면에 걸친 일
const SYSTEM_NOTE = T(["*"], ["overview", "markets", "llm-profile", "llm-costs", "schedule"]);
// 내가 보낸 메시지 — 매니저의 최근 대화 메모(구조 화면)
const USER_NOTE = T(["agent-structure"]);

/** 채팅 한 줄 → 다시 받을 캐시 (없으면 null: 매니저 생각·도구 기록·리스크 안내 등은 화면 데이터가 바뀌지 않는다) */
export function chatTargets(m: ChatMsg): Targets | null {
  if (m.kind === "fill") return FILL;
  if (m.kind === "decision") return DECISION;
  if (m.kind === "summary") return m.role === "system" ? DAILY_REPORT : SESSION_DONE;
  switch (m.role) {
    case "broker":
      return ORDER;
    case "system":
      return m.run_id != null ? SESSION_NOTE : SYSTEM_NOTE;
    case "user":
      return USER_NOTE;
    default:
      return null; // analyst 생각·도구, risk 차단·조정
  }
}

/** 상태 이벤트 (runner·engine 의 hub.state) → 다시 받을 캐시 */
export function stateTargets(data: Record<string, unknown>): Targets {
  const market = new Set<string>();
  const global = new Set<string>(["overview"]);
  if ("agent_running" in data) {
    ["system", "runs", "agent-structure"].forEach((k) => market.add(k));
    global.add("markets");
  }
  if ("engine_status" in data) {
    ["system", "agent-structure"].forEach((k) => market.add(k));
    global.add("markets");
  }
  if ("equity" in data || "cash" in data) ["portfolio", "equity"].forEach((k) => market.add(k));
  if (market.size === 0) {
    market.add("system");
    global.add("markets");
  }
  return { market: [...market], global: [...global] };
}

/** 웹소켓 한 줄 → 다시 받을 캐시. 진행·입력 중·감사 로그·인사는 null (자주 오거나 캐시와 무관) */
export function targetsFor(msg: WsMsg): Targets | null {
  if (msg.channel === "chat") return chatTargets(msg.data);
  if (msg.channel === "state") return stateTargets(msg.data);
  return null;
}

/** 모아 둔 이름들 */
export class PendingTargets {
  market = new Set<string>();
  global = new Set<string>();
  add(t: Targets) {
    t.market.forEach((k) => this.market.add(k));
    t.global.forEach((k) => this.global.add(k));
  }
  get empty() {
    return this.market.size === 0 && this.global.size === 0;
  }
  clear() {
    this.market.clear();
    this.global.clear();
  }
}

/** 쿼리 키가 이 시장의 이 이름들에 해당하는지 */
export function matchesTargets(
  key: readonly unknown[],
  market: MarketCode,
  t: { market: ReadonlySet<string> | readonly string[]; global: ReadonlySet<string> | readonly string[] },
): boolean {
  const has = (s: ReadonlySet<string> | readonly string[], v: string) =>
    "has" in s ? s.has(v) : (s as readonly string[]).includes(v);
  const [k0, k1] = key;
  if (isMarketCode(k0)) return k0 === market && (has(t.market, "*") || has(t.market, String(k1)));
  if (!has(t.global, String(k0))) return false;
  // ["schedule", "US"] 처럼 시장이 붙은 공통 키는 그 시장 것만
  return !isMarketCode(k1) || k1 === market;
}

/** 모아 둔 캐시를 한 번에 다시 받게 한다 (화면에 떠 있지 않은 캐시는 표시만 해 두고 다음에 열 때 받는다) */
export function invalidateTargets(qc: QueryClient, market: MarketCode, t: PendingTargets | Targets) {
  return qc.invalidateQueries({ predicate: (q) => matchesTargets(q.queryKey, market, t) });
}
