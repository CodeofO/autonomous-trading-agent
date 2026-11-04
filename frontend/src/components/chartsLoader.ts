import { lazyWithPreload } from "../lazyPage";

// 차트 라이브러리(lightweight-charts, 약 160KB)는 차트를 그리는 화면에서만 받는다 — vite.config.ts 의 'charts' 조각
let loading: Promise<typeof import("./charts")> | undefined;

export const loadCharts = () => {
  loading ??= import("./charts").catch((e: unknown) => {
    loading = undefined;
    throw e;
  });
  return loading;
};

/** 차트 화면 조각을 받기 시작할 때 같이 받기 시작한다 (화면이 그려지는 동안 차트 조각이 도착하도록) */
export const preloadCharts = () => {
  loadCharts().catch(() => {});
};

export const EquityChartLazy = lazyWithPreload(loadCharts, (m) => m.EquityChart);
export const CandleChartLazy = lazyWithPreload(loadCharts, (m) => m.CandleChart);
