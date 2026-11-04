import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { EventsContext } from "./live/hooks";
import { EventsStore } from "./live/store";
import { isMarketCode, useMarket } from "./market";

/** 웹소켓이 연결돼 있을 때 캐시를 '신선'으로 보는 시간 — 바뀌면 이벤트가 알려 주므로 화면을 옮길 때마다 다시 받지 않는다 */
export const LIVE_STALE_MS = 30_000;

const marketOfKey = (key: QueryKey) => {
  const [k0, k1] = key;
  return isMarketCode(k0) ? k0 : isMarketCode(k1) ? k1 : null;
};

/**
 * 시장마다 웹소켓 하나씩 — 미장이 켜져 있으면 두 시장을 늘 같이 듣는다.
 * 그래서 시장을 바꾸면 대화·진행 상태가 바로 보이고, 한눈에 보기는 두 시장의 소식을 실시간으로 받는다.
 * 저장소·구독 방법은 live/store.ts · live/hooks.ts
 */
export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { available } = useMarket();
  const [store] = useState(() => new EventsStore(qc));
  const usOn = available.includes("US");

  useEffect(() => () => store.stopAll(), [store]);
  useEffect(() => {
    store.stream("KR").setEnabled(true);
    store.stream("US").setEnabled(usOn);
  }, [store, usOn]);

  // 연결돼 있는 시장의 캐시는 LIVE_STALE_MS 동안 다시 받지 않는다 (직접 정한 staleTime 은 그대로)
  useEffect(() => {
    const prev = qc.getDefaultOptions();
    const base = prev.queries?.staleTime;
    qc.setDefaultOptions({
      ...prev,
      queries: {
        ...prev.queries,
        staleTime: (q) =>
          store.isLive(marketOfKey(q.queryKey))
            ? LIVE_STALE_MS
            : typeof base === "function"
              ? base(q)
              : (base ?? 0),
      },
    });
    return () => qc.setDefaultOptions(prev);
  }, [qc, store]);

  return <EventsContext.Provider value={store}>{children}</EventsContext.Provider>;
}
