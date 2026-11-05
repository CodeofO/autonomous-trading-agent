import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { clearToasts } from "../ui/toast";

// 토스트는 앱 전체 저장소에 쌓인다 — 테스트 사이에 남지 않게 비운다
afterEach(() => clearToasts());

// jsdom 에는 canvas 가 없어 차트 라이브러리는 가짜로 대체
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), applyOptions: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  };
  return {
    createChart: vi.fn(() => chart),
    createSeriesMarkers: vi.fn(),
    CandlestickSeries: {},
    AreaSeries: {},
    HistogramSeries: {},
    LineSeries: {},
    ColorType: { Solid: "solid" },
  };
});
