import { useState } from "react";
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { ChartData, ChartInterval } from "../api/types";
import { readStore, writeStore } from "../market";

/** 서버가 주기 전(첫 응답 전)에 보여 줄 봉 단위 버튼 — 서버 목록(backend app/market/chart.py)과 같다 */
export const CHART_INTERVALS: ChartInterval[] = [
  { key: "1m", label: "1분", intraday: true, enabled: true },
  { key: "5m", label: "5분", intraday: true, enabled: true },
  { key: "15m", label: "15분", intraday: true, enabled: true },
  { key: "30m", label: "30분", intraday: true, enabled: true },
  { key: "60m", label: "1시간", intraday: true, enabled: true },
  { key: "1d", label: "일", intraday: false, enabled: true },
  { key: "1w", label: "주", intraday: false, enabled: true },
  { key: "1M", label: "월", intraday: false, enabled: true },
];
const KEYS = new Set(CHART_INTERVALS.map((x) => x.key));
/** 분봉은 30초, 일·주·월은 5분마다 다시 받는다 (장 밖에서도 시간외·데이마켓 봉이 생긴다) */
const MINUTE_POLL_MS = 30_000;
const DAILY_POLL_MS = 300_000;

export const isIntraday = (key: string) => CHART_INTERVALS.find((x) => x.key === key)?.intraday ?? false;

/** 고른 봉 단위 (이 기기에 storageKey 마다 기억). minutes=false 면 분 단위는 기억해 둔 값이어도 일봉으로 */
export function useChartInterval(storageKey: string, minutes = true): [string, (k: string) => void] {
  const [interval, setIntervalKey] = useState(() => {
    const saved = readStore(storageKey);
    const ok = saved && KEYS.has(saved) && (minutes || !isIntraday(saved));
    return ok ? saved : "1d";
  });
  const pick = (k: string) => {
    setIntervalKey(k);
    writeStore(storageKey, k);
  };
  return [interval, pick];
}

/** 봉 단위 하나의 차트 데이터 (분봉 30초·일봉 5분마다 다시, slow 면 그 두 배 간격) */
export function useChartData(
  queryKey: unknown[],
  load: (interval: string) => Promise<ChartData>,
  interval: string,
  { slow = false }: { slow?: boolean } = {},
): UseQueryResult<ChartData> {
  const every = isIntraday(interval) ? MINUTE_POLL_MS : DAILY_POLL_MS;
  return useQuery({
    queryKey: [...queryKey, "chart", interval],
    queryFn: () => load(interval),
    refetchInterval: slow ? every * 2 : every,
    placeholderData: keepPreviousData, // 단위를 바꾸는 동안 이전 차트를 그대로 (자리가 출렁이지 않게)
  });
}
