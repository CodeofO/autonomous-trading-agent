import { describe, expect, it } from "vitest";
import { koCrosshair, koTick, periodOf, TICK, timeParts } from "../utils/chartTime";

// 2026-06-22(월) 14:35 한국 시각을 벽시계 초로 (UTC 로 읽으면 한국 시각)
const KST_WALL = Date.UTC(2026, 5, 22, 14, 35, 7) / 1000;

describe("차트 시각을 한국식으로 (D-65)", () => {
  it("숫자·날짜 글자·BusinessDay 모두 읽는다", () => {
    expect(timeParts(KST_WALL)).toMatchObject({ y: 2026, m: 6, d: 22, hh: 14, mm: 35, dow: 1, clock: true });
    expect(timeParts("2026-06-22")).toMatchObject({ y: 2026, m: 6, d: 22, dow: 1, clock: false });
    expect(timeParts({ year: 2026, month: 6, day: 22 })).toMatchObject({ y: 2026, m: 6, d: 22, dow: 1 });
    expect(timeParts("어제")).toBeNull();
  });

  it("십자선: 일봉은 날짜(요일), 분봉은 시각까지, 주·월봉은 기간", () => {
    expect(koCrosshair("2026-06-22", "day")).toBe("2026년 6월 22일 (월)");
    expect(koCrosshair(KST_WALL, "min")).toBe("2026년 6월 22일 (월) 14:35");
    expect(koCrosshair("2026-06-22", "week")).toBe("2026년 6월 22일 주");
    expect(koCrosshair("2026-06-01", "month")).toBe("2026년 6월");
    // 날짜만 있는 값에는 분봉이라도 시각을 붙이지 않는다
    expect(koCrosshair("2026-06-22", "min")).toBe("2026년 6월 22일 (월)");
  });

  it("가로축 눈금: 2026년 · 6월 · 22일 · 14:35", () => {
    expect(koTick("2026-01-02", TICK.year)).toBe("2026년");
    expect(koTick("2026-06-01", TICK.month)).toBe("6월");
    expect(koTick("2026-06-22", TICK.day)).toBe("22일");
    expect(koTick(KST_WALL, TICK.time)).toBe("14:35");
    expect(koTick(KST_WALL, TICK.seconds)).toBe("14:35:07");
    expect(koTick(null, TICK.day)).toBeNull();
  });

  it("봉 단위 키 → 기간", () => {
    expect(periodOf("1m")).toBe("min");
    expect(periodOf("60m")).toBe("min");
    expect(periodOf("1d")).toBe("day");
    expect(periodOf("1w")).toBe("week");
    expect(periodOf("1M")).toBe("month");
    expect(periodOf(undefined, true)).toBe("min");
    expect(periodOf(undefined)).toBe("day");
  });
});
