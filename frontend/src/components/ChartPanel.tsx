import { useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type { ChartData, ChartInterval, Currency } from "../api/types";
import { readStore, writeStore } from "../market";
import { CandleChart, ChartPlaceholder } from "./LazyCharts";

/** 서버가 주기 전(첫 응답 전)에 보여 줄 봉 단위 버튼 — 서버 목록(backend app/market/chart.py)과 같다 */
const CHART_INTERVALS: ChartInterval[] = [
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

/**
 * 봉 단위를 고르는 차트 (D-63) — 증권사 차트처럼 1·5·15·30분·1시간, 일·주·월.
 * 고른 단위는 이 기기에 기억한다(storageKey 마다). 토스 Open API 는 1분·일봉만 주므로 나머지는 서버가 모아서 만든다.
 */
export function ChartPanel({
  queryKey,
  load,
  currency,
  digits,
  storageKey = "chart.interval",
  minutes = true,
  legend,
}: {
  /** 쿼리 키 앞부분 (봉 단위가 뒤에 붙는다) */
  queryKey: unknown[];
  /** 봉 단위 → 차트 데이터 (시장별 주소는 부르는 쪽이 정한다) */
  load: (interval: string) => Promise<ChartData>;
  currency?: Currency;
  digits?: number;
  storageKey?: string;
  /** false 면 분 단위 버튼을 막는다 (국고채) */
  minutes?: boolean;
  legend?: ReactNode;
}) {
  const [interval, setIntervalKey] = useState(() => {
    const saved = readStore(storageKey);
    const ok = saved && KEYS.has(saved) && (minutes || !saved.endsWith("m") || saved === "1M");
    return ok ? saved : "1d";
  });
  const pick = (k: string) => {
    setIntervalKey(k);
    writeStore(storageKey, k);
  };
  const intraday = CHART_INTERVALS.find((x) => x.key === interval)?.intraday ?? false;
  const q = useQuery({
    queryKey: [...queryKey, "chart", interval],
    queryFn: () => load(interval),
    refetchInterval: intraday ? MINUTE_POLL_MS : DAILY_POLL_MS,
    placeholderData: keepPreviousData, // 단위를 바꾸는 동안 이전 차트를 그대로 (자리가 출렁이지 않게)
  });
  const options = (q.data?.intervals ?? CHART_INTERVALS).map((x) => ({
    ...x,
    enabled: x.enabled && (minutes || !x.intraday),
  }));
  const data = q.data;
  return (
    <div className="chart-panel">
      <div className="seg chart-intervals" role="group" aria-label="봉 단위">
        {options.map((x, i) => (
          <button
            key={x.key}
            type="button"
            className={`${x.key === interval ? "active" : ""}${i > 0 && !x.intraday && options[i - 1].intraday ? " gap" : ""}`}
            aria-pressed={x.key === interval}
            disabled={!x.enabled}
            onClick={() => pick(x.key)}
          >
            {x.label}
          </button>
        ))}
      </div>
      {data ? (
        <div className={q.isPlaceholderData ? "chart-stale" : undefined}>
          {data.bars.length > 0 ? (
            <CandleChart
              bars={data.bars}
              markers={data.markers}
              currency={currency}
              digits={digits}
              intraday={data.intraday}
            />
          ) : (
            <p className="muted chart-empty">
              이 단위로는 아직 봉이 없어요 (거래가 없었던 시간일 수 있어요).
            </p>
          )}
        </div>
      ) : q.isError ? (
        <p className="error chart-empty">차트를 불러오지 못했어요: {errorText(q.error)}</p>
      ) : (
        <ChartPlaceholder />
      )}
      {legend}
    </div>
  );
}
