import type { ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type { ChartData, ChartInterval, Currency } from "../api/types";
import { periodOf } from "../utils/chartTime";
import { CHART_INTERVALS, useChartData, useChartInterval } from "./chartData";
import { CandleChart, ChartPlaceholder } from "./LazyCharts";

/** 봉 단위 버튼 줄 (1·5·15·30분·1시간 | 일·주·월) */
export function IntervalPicker({
  value,
  onPick,
  options = CHART_INTERVALS,
  minutes = true,
}: {
  value: string;
  onPick: (k: string) => void;
  options?: ChartInterval[];
  /** false 면 분 단위 버튼을 막는다 (국고채) */
  minutes?: boolean;
}) {
  const shown = options.map((x) => ({ ...x, enabled: x.enabled && (minutes || !x.intraday) }));
  return (
    <div className="seg chart-intervals" role="group" aria-label="봉 단위">
      {shown.map((x, i) => (
        <button
          key={x.key}
          type="button"
          className={`${x.key === value ? "active" : ""}${i > 0 && !x.intraday && shown[i - 1].intraday ? " gap" : ""}`}
          aria-pressed={x.key === value}
          disabled={!x.enabled}
          onClick={() => onPick(x.key)}
        >
          {x.label}
        </button>
      ))}
    </div>
  );
}

/** 받은 차트 데이터를 그린다 (받는 중이면 같은 크기 빈 자리, 실패면 사유) */
export function ChartView({
  q,
  currency,
  digits,
}: {
  q: UseQueryResult<ChartData>;
  currency?: Currency;
  digits?: number;
}) {
  const data = q.data;
  if (data) {
    return (
      <div className={q.isPlaceholderData ? "chart-stale" : undefined}>
        {data.bars.length > 0 ? (
          <CandleChart
            bars={data.bars}
            markers={data.markers}
            currency={currency}
            digits={digits}
            intraday={data.intraday}
            period={periodOf(data.interval, data.intraday)}
          />
        ) : (
          <p className="muted chart-empty">이 단위로는 아직 봉이 없어요 (거래가 없었던 시간일 수 있어요).</p>
        )}
      </div>
    );
  }
  if (q.isError) return <p className="error chart-empty">차트를 불러오지 못했어요: {errorText(q.error)}</p>;
  return <ChartPlaceholder />;
}

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
  const [interval, pick] = useChartInterval(storageKey, minutes);
  const q = useChartData(queryKey, load, interval);
  return (
    <div className="chart-panel">
      <IntervalPicker value={interval} onPick={pick} options={q.data?.intervals} minutes={minutes} />
      <ChartView q={q} currency={currency} digits={digits} />
      {legend}
    </div>
  );
}
