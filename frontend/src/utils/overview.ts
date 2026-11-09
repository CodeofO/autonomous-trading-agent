import type { QueryClient } from "@tanstack/react-query";
import { get } from "../api/client";
import type { ChatMsg, MarketCode, Overview } from "../api/types";

// 한눈에 보기('전체') 화면의 계산 — 화면(pages/Overview.tsx)과 테스트가 같이 쓴다

export const OVERVIEW_KEY = ["overview"];

/**
 * 첫 화면이 뜨고 한가해지면 '전체' 요약(~600B)을 한 번 받아 둔다.
 * 측정: 미장→전체 첫 전환에서 총자산이 요약 요청을 그때 시작하느라 180→349ms 로 늦어졌었다
 * (docs/perf/REFACTOR_REPORT.md). 두 시장이 다 켜져 있을 때만('전체' 화면이 있을 때만) 받는다.
 */
export function prefetchOverviewWhenIdle(qc: QueryClient, markets: MarketCode[], delayMs = 2500): () => void {
  if (markets.length < 2) return () => {};
  const t = window.setTimeout(() => {
    void qc.prefetchQuery({
      queryKey: OVERVIEW_KEY,
      queryFn: () => get<Overview>("/api/overview"),
      staleTime: 30_000,
    });
  }, delayMs);
  return () => window.clearTimeout(t);
}

/** 시간표 시작: 한국 시각 06:00 (국장 낮 → 미장 밤 → 다음날 06:00) */
export const DAY_START = 6 * 60;
const KST_OFFSET = 9 * 60;

/** 한국 시각 하루 중 몇 분째인지 */
export const kstMinutes = (ms: number) => (((Math.floor(ms / 60_000) + KST_OFFSET) % 1440) + 1440) % 1440;

/** 시간표 위 위치(%): 06:00 = 0 · 다음날 06:00 = 100 */
export const timelinePos = (ms: number) => (((kstMinutes(ms) - DAY_START + 1440) % 1440) / 1440) * 100;

/** 지금이 들어 있는 시간표 칸의 시작(한국 시각 06:00) 시각 ms — 끝은 +24시간 */
export const timelineStart = (ms: number) => {
  const minute = Math.floor(ms / 60_000) * 60_000;
  return minute - ((kstMinutes(ms) - DAY_START + 1440) % 1440) * 60_000;
};

export const FEED_LIMIT = 15;

/** 소식에 올릴 메시지: 매수·매도 결정, 체결, 세션 정리·마감 리포트 (유지 결정은 세션 정리에 담겨 있어 뺀다) */
export const isFeedItem = (m: ChatMsg) =>
  m.kind === "fill" ||
  m.kind === "summary" ||
  (m.kind === "decision" && (m.payload.action === "BUY" || m.payload.action === "SELL"));

/** 두 시장의 소식을 섞어 최신 순으로 */
export function mergeFeed(
  lists: { code: MarketCode; messages: ChatMsg[] }[],
  limit = FEED_LIMIT,
): { code: MarketCode; msg: ChatMsg }[] {
  return lists
    .flatMap(({ code, messages }) => messages.filter(isFeedItem).map((msg) => ({ code, msg })))
    .sort((a, b) => Date.parse(b.msg.ts) - Date.parse(a.msg.ts))
    .slice(0, limit);
}
