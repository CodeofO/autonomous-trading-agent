import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScheduleInfo, ScheduleUpdate } from "../api/types";
import { ScheduleSettings } from "../pages/ScheduleSettings";
import {
  fromMinutes,
  intervalLabel,
  kstShift,
  kstText,
  outsideMarketTimes,
  scheduleTimes,
  toMinutes,
  tzOffsetMinutes,
  validateSchedule,
} from "../utils/schedule";
import { apiError, mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const INTERVALS = [5, 10, 15, 20, 30, 60, 120, 180, 240];
const KR_HOURS = { open: "09:00", close: "15:30", order_cutoff: "15:20" };
const US_HOURS = { open: "09:30", close: "16:00", order_cutoff: "15:50" };

/** 서버처럼 시각 목록을 채운다 (테스트용) */
function info(
  base: Pick<ScheduleInfo, "market" | "timezone" | "tz_label" | "market_hours" | "report_at">,
  v: ScheduleUpdate,
  custom: boolean,
  shift = 0,
): ScheduleInfo {
  const times = scheduleTimes(v.start, v.end, v.interval_minutes);
  return {
    ...base,
    ...v,
    custom,
    intervals: INTERVALS,
    times,
    times_kst: times.map((t) => fromMinutes(toMinutes(t) + shift)),
    outside_market: outsideMarketTimes(times, base.market_hours),
    next_runs: [
      { name: "판단 세션", at: "2026-11-04T16:00:00+09:00" },
      { name: "마감 리포트", at: "2026-11-04T15:45:00+09:00" },
    ],
  };
}

const KR_BASE = {
  market: "KR" as const,
  timezone: "Asia/Seoul",
  tz_label: "한국 시각",
  market_hours: KR_HOURS,
  report_at: "15:45",
};
const US_BASE = {
  market: "US" as const,
  timezone: "America/New_York",
  tz_label: "미국 동부 시각",
  market_hours: US_HOURS,
  report_at: "16:15",
};
const KR = info(KR_BASE, { start: "08:00", end: "17:00", interval_minutes: 60 }, true);
const KR_DEFAULT = info(KR_BASE, { start: "09:00", end: "15:00", interval_minutes: 60 }, false);
// 서머타임(EDT): 미국 동부 09:30 = 한국 22:30
const US = info(US_BASE, { start: "09:30", end: "16:00", interval_minutes: 30 }, false, 13 * 60);

function scheduleServer(opts: { us?: boolean; kr404?: boolean; put?: () => Response } = {}) {
  let kr = KR;
  let us = US;
  return mockFetch({
    "/api/schedule": (_u: string, init?: RequestInit) => {
      if (opts.kr404) return apiError(404, "Not Found");
      const method = init?.method ?? "GET";
      if (method === "PUT") {
        if (opts.put) return opts.put();
        kr = info(KR_BASE, JSON.parse(String(init?.body)), true);
      } else if (method === "DELETE") kr = KR_DEFAULT;
      return kr;
    },
    "/api/us/schedule": (_u: string, init?: RequestInit) => {
      if (!opts.us) return apiError(404, "Not Found");
      if ((init?.method ?? "GET") === "PUT")
        us = info(US_BASE, JSON.parse(String(init?.body)), true, 13 * 60);
      return us;
    },
  });
}

const sent = (fetchMock: ReturnType<typeof mockFetch>, method: string) =>
  fetchMock.mock.calls
    .filter(([, i]) => (i?.method ?? "GET") === method)
    .map(([u, i]) => ({ url: String(u), body: i?.body ? JSON.parse(String(i.body)) : undefined }));

const chips = () => within(screen.getByRole("list", { name: "판단 시각" })).getAllByRole("listitem");
const chipTexts = () => chips().map((c) => c.firstChild?.textContent);

describe("판단 시각 계산 (서버와 같은 규칙)", () => {
  it("자정부터 주기 단위로 센 딱 떨어지는 시각 중 [시작, 끝] 안의 것", () => {
    const hourly = scheduleTimes("08:00", "17:00", 60);
    expect(hourly).toHaveLength(10);
    expect(hourly[0]).toBe("08:00");
    expect(hourly[9]).toBe("17:00");
    expect(scheduleTimes("08:10", "09:00", 15)).toEqual(["08:15", "08:30", "08:45", "09:00"]);
    expect(scheduleTimes("08:00", "09:00", 15)).toEqual(["08:00", "08:15", "08:30", "08:45", "09:00"]);
    expect(scheduleTimes("08:00", "17:00", 180)).toEqual(["09:00", "12:00", "15:00"]);
    expect(scheduleTimes("09:00", "09:00", 60)).toEqual(["09:00"]);
    expect(scheduleTimes("10:00", "09:00", 60)).toEqual([]);
    expect(scheduleTimes("08:05", "08:55", 60)).toEqual([]);
  });

  it("장 시작 30분 전보다 이르거나 주문 마감 시각부터(마감 시각 포함) 장 시간 밖", () => {
    expect(
      outsideMarketTimes(["08:00", "08:29", "08:30", "09:00", "15:15", "15:20", "15:21", "17:00"], KR_HOURS),
    ).toEqual(["08:00", "08:29", "15:20", "15:21", "17:00"]);
    // 미장: 15:50 마감부터 주문을 받지 않는다 (서버와 같은 >=)
    expect(outsideMarketTimes(["09:00", "09:30", "15:45", "15:50"], US_HOURS)).toEqual(["15:50"]);
  });

  it("저장 전 검사: 5분 단위 · 끝 > 시작 · 고를 수 있는 주기 · 하루 96회 이하", () => {
    expect(validateSchedule("08:00", "17:00", 60, INTERVALS)).toBeNull();
    expect(validateSchedule("08:03", "17:00", 60, INTERVALS)).toMatch(/5분 단위/);
    expect(validateSchedule("17:00", "17:00", 60, INTERVALS)).toMatch(/끝 시각은 시작 시각보다 늦어야/);
    expect(validateSchedule("08:00", "17:00", 7, INTERVALS)).toMatch(/고를 수 없는 주기/);
    expect(validateSchedule("00:00", "23:55", 5, INTERVALS)).toMatch(/288회로 너무 많아요 \(최대 96회\)/);
    expect(validateSchedule("08:05", "08:55", 60, INTERVALS)).toMatch(/판단 시각이 하나도 없어요/);
  });

  it("주기 이름과 한국 시각 변환", () => {
    expect(intervalLabel(5)).toBe("5분");
    expect(intervalLabel(15)).toBe("15분");
    expect(intervalLabel(60)).toBe("1시간");
    expect(intervalLabel(90)).toBe("1시간 30분");
    expect(intervalLabel(120)).toBe("2시간");
    expect(kstShift(US)).toBe(780);
    expect(kstShift(KR)).toBe(0);
    expect(kstText("09:30", 780)).toBe("22:30");
    expect(kstText("16:00", 780)).toBe("05:00(+1일)");
    expect(tzOffsetMinutes("Asia/Seoul")).toBe(540);
    // 서버가 시각을 안 줬을 때는 브라우저로 계산 (1월은 서머타임 아님: 14시간 차)
    expect(kstShift({ ...US, times: [], times_kst: [] }, new Date("2026-01-15T12:00:00Z"))).toBe(840);
  });
});

describe("⏰ 판단 주기 카드", () => {
  it("국장 일정을 미리 보기로 보여 주고, 미장이 꺼져 있으면(404) 미장 탭을 숨긴다", async () => {
    const fetchMock = scheduleServer();
    renderWithProviders(<ScheduleSettings />);
    expect(await screen.findByRole("heading", { name: "⏰ 판단 주기" })).toBeInTheDocument();
    await screen.findByRole("list", { name: "판단 시각" });
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/us/schedule")).toBe(true),
    );
    expect(screen.getByRole("tab", { name: "국장" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.queryByRole("tab", { name: "미장" })).not.toBeInTheDocument());

    expect(screen.getByText(/15:33에 설정을 바꿔도 15:33에 바로 돌지 않고/)).toBeInTheDocument();
    expect(screen.getByTestId("sched-count")).toHaveTextContent("하루 10회");
    expect(chipTexts()).toEqual(KR.times);
    // 08:00(장 시작 30분 전보다 이름) · 16:00 · 17:00(주문 마감 뒤)은 경고 표시
    const out = chips().filter((c) => c.classList.contains("sched-out"));
    expect(out.map((c) => c.textContent)).toEqual(["08:00", "16:00", "17:00"]);
    expect(screen.getByText("장 시간 밖 — 분석만 하고 주문은 다음 장에")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LLM 비용 화면" })).toHaveAttribute("href", "/costs");
    expect(screen.getByText("화면에서 정한 값")).toBeInTheDocument();
    const next = within(screen.getByRole("list", { name: "다음 실행" })).getAllByRole("listitem");
    expect(next[0]).toHaveTextContent("판단 세션");
    // 저장된 값 그대로면 저장할 게 없다
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
    expect(screen.getByLabelText(/시작/)).toHaveValue("08:00");
    expect(within(screen.getByLabelText("주기")).getByRole("option", { name: "1시간" })).toBeInTheDocument();
  });

  it("시각·주기를 바꾸면 같은 규칙으로 미리 보고, 저장하면 PUT 으로 보낸다", async () => {
    const fetchMock = scheduleServer();
    renderWithProviders(<ScheduleSettings />);
    await screen.findByRole("list", { name: "판단 시각" });
    fireEvent.change(screen.getByLabelText(/시작/), { target: { value: "08:10" } });
    fireEvent.change(screen.getByLabelText(/끝/), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("주기"), { target: { value: "15" } });

    expect(chipTexts()).toEqual(["08:15", "08:30", "08:45", "09:00"]);
    expect(screen.getByTestId("sched-count")).toHaveTextContent("하루 4회");
    expect(screen.getByText("저장하면 이렇게 바뀌어요")).toBeInTheDocument();
    // 화면에서 계산한 장 시간 밖: 08:15 (08:30 전)
    expect(
      chips()
        .filter((c) => c.classList.contains("sched-out"))
        .map((c) => c.textContent),
    ).toEqual(["08:15"]);

    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/저장했어요 ✓ 다음 슬롯부터 적용돼요/)).toBeInTheDocument();
    expect(sent(fetchMock, "PUT")).toEqual([
      { url: "/api/schedule", body: { start: "08:10", end: "09:00", interval_minutes: 15 } },
    ]);
    expect(screen.getByText("지금 일정")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("서버가 거절하면(400) 이유를 보여 준다", async () => {
    scheduleServer({ put: () => apiError(400, "판단 세션이 하루 96회를 넘어요") });
    renderWithProviders(<ScheduleSettings />);
    await screen.findByRole("list", { name: "판단 시각" });
    fireEvent.change(screen.getByLabelText("주기"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText("판단 세션이 하루 96회를 넘어요")).toBeInTheDocument();
    // 바꾸던 값은 그대로 남는다
    expect(screen.getByLabelText("주기")).toHaveValue("30");
  });

  it("끝이 시작보다 이르면 저장 요청 없이 알려 준다", async () => {
    const fetchMock = scheduleServer();
    renderWithProviders(<ScheduleSettings />);
    await screen.findByRole("list", { name: "판단 시각" });
    fireEvent.change(screen.getByLabelText(/끝/), { target: { value: "07:00" } });
    expect(screen.getByRole("alert")).toHaveTextContent("끝 시각은 시작 시각보다 늦어야 해요");
    const save = screen.getByRole("button", { name: "저장" });
    expect(save).toBeDisabled();
    fireEvent.submit(save.closest("form")!);
    expect(sent(fetchMock, "PUT")).toHaveLength(0);
  });

  it("기본값으로 되돌리면 DELETE 를 보내고 .env 기본값을 보여 준다", async () => {
    const fetchMock = scheduleServer();
    renderWithProviders(<ScheduleSettings />);
    await screen.findByRole("list", { name: "판단 시각" });
    fireEvent.click(screen.getByRole("button", { name: "기본값으로" }));
    expect(await screen.findByText("기본값으로 되돌렸어요 ✓")).toBeInTheDocument();
    expect(sent(fetchMock, "DELETE")).toEqual([{ url: "/api/schedule", body: undefined }]);
    expect(screen.getByText("기본값 (.env)")).toBeInTheDocument();
    expect(chipTexts()).toEqual(KR_DEFAULT.times);
    // 기본값이고 바꾼 것도 없으면 누를 게 없다
    expect(screen.getByRole("button", { name: "기본값으로" })).toBeDisabled();
  });

  it("미장 탭: 미국 동부 시각으로 고르고 한국 시각을 함께 보여 준다", async () => {
    const fetchMock = scheduleServer({ us: true });
    renderWithProviders(<ScheduleSettings />);
    fireEvent.click(await screen.findByRole("tab", { name: "미장" }));
    expect(screen.getByRole("tab", { name: "미장" })).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByText(/09:30~16:00 · 주문 마감 15:50 \(미국 동부 시각 · 한국 22:30~05:00\(\+1일\)\)/),
    ).toBeInTheDocument();
    const start = screen.getByLabelText(/시작/);
    expect(screen.getByText("시작")).toHaveTextContent("시작 (미국 동부 시각)");
    expect(within(start).getByRole("option", { name: "09:30 (한국 22:30)" })).toBeInTheDocument();
    const first = chips()[0];
    expect(first).toHaveTextContent("09:30");
    expect(first).toHaveTextContent("한국 22:30");
    expect(chips()[chips().length - 1]).toHaveTextContent("한국 05:00(+1일)");
    expect(chips()[chips().length - 1]).toHaveClass("sched-out"); // 16:00 은 주문 마감(15:50) 뒤

    fireEvent.change(screen.getByLabelText("주기"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() =>
      expect(sent(fetchMock, "PUT")).toEqual([
        { url: "/api/us/schedule", body: { start: "09:30", end: "16:00", interval_minutes: 60 } },
      ]),
    );
  });

  it("예전 서버(404)면 카드를 그리지 않는다", async () => {
    const fetchMock = scheduleServer({ kr404: true });
    renderWithProviders(<ScheduleSettings />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "⏰ 판단 주기" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
  });
});
