import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { clearToasts } from "../ui/toast";

// 토스트는 앱 전체 저장소에 쌓인다 — 테스트 사이에 남지 않게 비운다
afterEach(() => clearToasts());

// jsdom 에는 canvas 가 없어 차트 라이브러리는 가짜로 대체
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), applyOptions: vi.fn() };
  // 가로축은 한 객체 — 테스트가 맞춤(fitContent)·보던 범위 지키기를 셀 수 있게
  const time = {
    fitContent: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 1 })),
    setVisibleLogicalRange: vi.fn(),
  };
  const chart = {
    addSeries: vi.fn(() => series),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => time),
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
