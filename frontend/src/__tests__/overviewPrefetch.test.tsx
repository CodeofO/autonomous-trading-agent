import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OVERVIEW_KEY, prefetchOverviewWhenIdle } from "../utils/overview";
import { mockFetch } from "./utils";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("'전체' 요약 미리 받기 (첫 시장 전환 지연 개선)", () => {
  it("두 시장이 켜져 있으면 한가할 때 한 번 받아 캐시에 둔다", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({
      "/api/overview": { fx: { usdkrw: 1343, at: "" }, markets: [], total_krw: { equity: 1, pnl_today: 0 } },
    });
    const qc = new QueryClient();
    prefetchOverviewWhenIdle(qc, ["KR", "US"], 100);
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(150);
    await vi.waitFor(() => expect(qc.getQueryData(OVERVIEW_KEY)).toBeTruthy());
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/overview"))).toHaveLength(1);
  });

  it("국장만 있으면('전체' 화면 없음) 받지 않는다", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({});
    const qc = new QueryClient();
    const cancel = prefetchOverviewWhenIdle(qc, ["KR"], 100);
    await vi.advanceTimersByTimeAsync(200);
    expect(fetchMock).not.toHaveBeenCalled();
    cancel();
  });
});
