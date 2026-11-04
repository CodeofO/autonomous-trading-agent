import { Suspense, type ComponentProps } from "react";
import type { CandleChart as CandleChartImpl, EquityChart as EquityChartImpl } from "./charts";
import { CandleChartLazy, EquityChartLazy } from "./chartsLoader";

/*
 * 차트 자리. 차트 조각을 받는 동안에도 같은 크기의 빈 자리를 먼저 그려 아래 내용이 밀리지 않는다.
 * 차트 화면 조각을 받기 시작할 때 차트 조각도 같이 받기 시작한다 (routes.ts) — 보통은 이미 도착해 있다
 */

export function EquityChart(props: ComponentProps<typeof EquityChartImpl>) {
  return (
    <Suspense fallback={<div className="chart" style={{ height: props.height ?? 220 }} aria-hidden />}>
      <EquityChartLazy {...props} />
    </Suspense>
  );
}

export function CandleChart(props: ComponentProps<typeof CandleChartImpl>) {
  return (
    <Suspense fallback={<ChartPlaceholder />}>
      <CandleChartLazy {...props} />
    </Suspense>
  );
}

/** 일봉 차트와 같은 크기의 빈 자리 (데이터·차트 조각을 기다리는 동안) */
export function ChartPlaceholder() {
  return <div className="chart chart-placeholder" aria-hidden />;
}
