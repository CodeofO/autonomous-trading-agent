import { screen, waitFor } from "@testing-library/react";
import { createChart } from "lightweight-charts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventsProvider } from "../events";
import { Dashboard } from "../pages/Dashboard";
import { withLivePoint } from "../utils/equity";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

// 자산 추이 끝 = 위 총자산 (미장 장외 거래 뒤 차트가 전날 마감에 멈춰 있던 문제)

afterEach(() => vi.unstubAllGlobals());

const SNAPS = [
  { t: "2026-11-04T20:50:52+00:00", v: 9965.82 },
  { t: "2026-11-04T21:00:52+00:00", v: 9960.82 },
];

describe("withLivePoint", () => {
  it("마지막 스냅샷보다 새 총자산이면 끝에 붙인다", () => {
    const out = withLivePoint(SNAPS, { as_of: "2026-11-05T20:48:01+09:00", equity: 10012.68 });
    expect(out).toHaveLength(3);
    expect(out.at(-1)).toEqual({ t: "2026-11-05T20:48:01+09:00", v: 10012.68 });
  });

  it("스냅샷이 더 새것이거나 같은 시각이면 그대로", () => {
    expect(withLivePoint(SNAPS, { as_of: "2026-11-04T21:00:52+00:00", equity: 1 })).toBe(SNAPS);
    expect(withLivePoint(SNAPS, { as_of: "2026-11-04T20:00:00+00:00", equity: 1 })).toBe(SNAPS);
  });

  it("스냅샷이 없거나 총자산을 아직 모르면 그대로", () => {
    expect(withLivePoint([], { as_of: "2026-11-05T20:48:01+09:00", equity: 10012.68 })).toEqual([]);
    expect(withLivePoint(SNAPS, undefined)).toBe(SNAPS);
    expect(withLivePoint(SNAPS, { as_of: "2026-11-05T20:48:01+09:00", equity: null })).toBe(SNAPS);
    expect(withLivePoint(SNAPS, { as_of: "아님", equity: 5 })).toBe(SNAPS);
  });
});

describe("대시보드 자산 추이", () => {
  it("차트 마지막 값이 총자산 카드와 같다", async () => {
    vi.stubGlobal(
      "WebSocket",
      class {
        static OPEN = 1;
        readyState = 0;
        onopen = null;
        onclose = null;
        onmessage = null;
        send() {}
        close() {}
      },
    );
    vi.mocked(createChart).mockClear();
    mockFetch({
      "/api/portfolio": {
        as_of: "2026-11-05T20:48:01+09:00",
        latest_price_day: "2026-11-05",
        cash: 833.44,
        available_cash: 833.44,
        equity: 10012.68,
        initial_cash: 10000,
        total_return_pct: 0.13,
        daily_pnl_pct: 0.52,
        positions: [],
        open_orders: [],
      },
      "/api/equity": SNAPS.map((p) => ({ ts: p.t, equity: p.v, cash: 2076.65 })),
      "/api/system": SYSTEM,
      "/api/agent/progress": { active: false, typing: null, replying: false },
      "/api/chat": [],
      "/api/market/indicators": { as_of: "2026-11-05T20:48:01+09:00", items: [] },
    });
    renderWithProviders(
      <EventsProvider>
        <Dashboard />
      </EventsProvider>,
    );
    await screen.findByText("총자산");
    await waitFor(() => {
      const chart = vi.mocked(createChart).mock.results.at(-1)?.value as
        | { addSeries: ReturnType<typeof vi.fn> }
        | undefined;
      const series = chart?.addSeries.mock.results.at(-1)?.value as
        | { setData: ReturnType<typeof vi.fn> }
        | undefined;
      const last = series?.setData.mock.calls.at(-1)?.[0];
      expect(last).toHaveLength(3);
      expect(last.at(-1).value).toBe(10012.68);
    });
  });
});
