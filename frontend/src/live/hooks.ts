import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import type { MarketCode } from "../api/types";
import { useMarket } from "../market";
import {
  OFFLINE_STORE,
  type EventsStore,
  type MarketEvents,
  type MarketStream,
  type Resource,
} from "./store";

/** 실시간 저장소 (EventsProvider 가 채운다. 밖에서는 연결하지 않는 빈 저장소) */
export const EventsContext = createContext<EventsStore>(OFFLINE_STORE);

export const useEventsStore = () => useContext(EventsContext);

/** 이 시장(기본: 지금 시장)의 실시간 흐름 */
function useStream(m?: MarketCode): MarketStream {
  const store = useEventsStore();
  const { market } = useMarket();
  return store.stream(m ?? market);
}

/** 조각 하나만 구독 — 그 조각이 바뀔 때만 다시 그린다 */
function useSlice<K extends keyof MarketEvents>(stream: MarketStream, key: K): MarketEvents[K] {
  return useSyncExternalStore(stream.subscribe, () => stream.getState()[key]);
}

/** 이 화면이 이 묶음을 보여 준다고 알린다 (처음이면 서버에서 받아 온다) */
function useNeed(stream: MarketStream, res: Resource) {
  useEffect(() => stream.acquire(res), [stream, res]);
}

/** 웹소켓 연결 상태만 (머리말·메뉴·폴링 주기) */
export function useConnected(m?: MarketCode): boolean {
  return useSlice(useStream(m), "connected");
}

/** 실시간 저장소 없이 그리는 곳(화면 하나만 그리는 테스트 등) — 받아 올 것이 없으니 '받음'으로 본다 */
const useOffline = () => useEventsStore() === OFFLINE_STORE;

/**
 * 채팅 화면용: 메시지·입력 중·진행 상태 (채팅 기록을 처음 쓰면 받아 온다).
 * ready: 기록을 한 번이라도 받았는지 — 받기 전에는 '아직 조용하네요' 대신 자리 표시
 */
export function useChat(m?: MarketCode) {
  const s = useStream(m);
  useNeed(s, "chat");
  const offline = useOffline();
  const messages = useSlice(s, "messages");
  const typing = useSlice(s, "typing");
  const progress = useSlice(s, "progress");
  const ready = useSlice(s, "chatReady") || offline;
  return useMemo(() => ({ messages, typing, progress, ready }), [messages, typing, progress, ready]);
}

/** 메시지만 (한눈에 보기 소식) — 진행·입력 중 표시가 바뀌어도 다시 그리지 않는다 */
export function useChatMessages(m?: MarketCode) {
  const s = useStream(m);
  useNeed(s, "chat");
  const offline = useOffline();
  const messages = useSlice(s, "messages");
  const ready = useSlice(s, "chatReady") || offline;
  return useMemo(() => ({ messages, ready }), [messages, ready]);
}

/** 진행 상태만 (세션 목록의 '중단됨' 판단) — 채팅 기록은 받지 않는다 */
export function useProgress(m?: MarketCode) {
  return useSlice(useStream(m), "progress");
}

/** 감사 로그 (이벤트 로그 화면에서만 받아 온다) */
export function useAudit(m?: MarketCode) {
  const s = useStream(m);
  useNeed(s, "audit");
  return useSlice(s, "audit");
}

/** 감사 로그를 한 번이라도 받았는지 (받기 전에는 빈 표 대신 자리 표시) */
export function useAuditReady(m?: MarketCode) {
  const offline = useOffline();
  const ready = useSlice(useStream(m), "auditReady");
  return ready || offline;
}

/**
 * 웹소켓이 연결돼 있으면 폴링을 늦춘다 — 바뀌는 일은 이벤트가 알려 주고(live/invalidation.ts),
 * 폴링은 시세처럼 이벤트가 없는 변화만 따라간다. 끊기면 원래 주기로.
 * 5초 → 30초, 10초 → 60초, 30초 이상 → 60초
 */
export function slowWhenLive(fast: number): number {
  return Math.min(60_000, Math.max(30_000, fast * 6));
}

/** 이 시장 연결 상태에 맞춘 폴링 주기 */
export function useLiveInterval(m: MarketCode | undefined, fast: number): number {
  return useConnected(m) ? slowWhenLive(fast) : fast;
}
