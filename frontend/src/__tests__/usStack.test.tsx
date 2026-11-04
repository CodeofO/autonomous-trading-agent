import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScheduleInfo } from "../api/types";
import { AgentPage } from "../pages/Agent";
import { MemoPage } from "../pages/Memo";
import { ScheduleSettings } from "../pages/ScheduleSettings";
import { looksLikeCode } from "../utils/memo";
import { isPreOpen } from "../utils/schedule";
import { BOTH_MARKETS, mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const structure = (schedule: Record<string, unknown>, memory?: Record<string, unknown>) => ({
  persona: { name: "알파", emoji: "🦉" },
  llm: { configured: true, model: "m", profile: "free", mode: "auto", max_steps: 24, max_tokens: 8192 },
  sources: [{ name: "토스증권", ready: true }],
  tools: [
    { name: "submit_decisions", description: "제출", group: "decision", source: "모의계좌", ready: true },
  ],
  schedule: { report: "06:15", monitor_minutes: 1, refresh_minutes: 10, ...schedule },
  risk: {
    max_position_weight: 0.2,
    max_order_amount: 1500,
    daily_loss_limit: -0.03,
    stop_loss: -0.05,
    take_profit: 0.1,
    min_order_interval_minutes: 60,
    max_positions: 10,
    min_cash_ratio: 0.05,
    allow_new_buys: true,
  },
  paper: { initial_cash: 10_000, commission_rate: 0, sell_tax_rate: 0, slippage_bps: 5 },
  trading_mode: "paper",
  live_daily_buy_limit: null,
  engine_status: "RUNNING",
  agent_running: false,
  ...(memory ? { memory } : {}),
});

const chipTexts = () =>
  [...document.querySelectorAll(".time-chips .chip")].map((c) => ({
    text: c.textContent,
    pre: c.classList.contains("ok"),
  }));

describe("판단 시각의 '장전' 표시", () => {
  it("장 시작 전 12시간 안이면 장전 (자정을 넘는 미장도)", () => {
    expect(isPreOpen("08:35", "09:00")).toBe(true);
    expect(isPreOpen("09:00", "09:00")).toBe(false);
    expect(isPreOpen("10:00", "09:00")).toBe(false);
    expect(isPreOpen("23:05", "23:30")).toBe(true);
    expect(isPreOpen("02:30", "23:30")).toBe(false);
    expect(isPreOpen("bad", "23:30")).toBe(false);
  });

  it("미장: 한국 시각 목록에서 개장(23:30) 전 슬롯만 장전, 나머지는 장중 · 금액은 달러", async () => {
    mockFetch({
      "/api/us/agent/structure": structure({
        interval_minutes: 180,
        morning: "23:05",
        sessions: ["02:30", "05:30"],
        times: ["23:05", "02:30", "05:30"],
        custom: false,
      }),
    });
    renderWithProviders(<AgentPage />, { markets: BOTH_MARKETS, market: "US" });
    expect(await screen.findByText("알파 매니저")).toBeInTheDocument();
    expect(chipTexts()).toEqual([
      { text: "23:05 장전", pre: true },
      { text: "02:30", pre: false },
      { text: "05:30", pre: false },
    ]);
    expect(screen.getByText(/장전 23:05 · 장중 180분마다 \(02:30~05:30, 2회\)/)).toBeInTheDocument();
    expect(screen.getByText("$1,500.00")).toBeInTheDocument();
    expect(screen.getByText("초기 자금 $10,000.00")).toBeInTheDocument();
  });

  it("직접 정한 일정의 첫 슬롯이 장중이면 '장전'이라 하지 않는다", async () => {
    mockFetch({
      "/api/agent/structure": structure({
        interval_minutes: 60,
        morning: "10:00",
        sessions: ["11:00", "12:00"],
        times: ["10:00", "11:00", "12:00"],
        custom: true,
      }),
    });
    renderWithProviders(<AgentPage />, { markets: BOTH_MARKETS, market: "KR" });
    expect(await screen.findByText("알파 매니저")).toBeInTheDocument();
    expect(chipTexts().every((c) => !c.pre && !c.text?.includes("장전"))).toBe(true);
    expect(screen.queryByText(/장전 10:00/)).not.toBeInTheDocument();
    expect(screen.getByText(/장중 60분마다 \(10:00~12:00, 3회\)/)).toBeInTheDocument();
  });

  it("최근 대화 메모를 꺼 두면 '최근 5개' 경고 대신 꺼져 있다고 알려 준다", async () => {
    const memory = { rules: [], sessions: [], memos: [], cards: [], use_recent_messages: false };
    mockFetch({
      "/api/agent/structure": structure(
        { interval_minutes: 60, morning: "08:35", sessions: ["09:00"], times: ["08:35", "09:00"] },
        memory,
      ),
    });
    renderWithProviders(<AgentPage />);
    expect(await screen.findByTestId("memos-off")).toHaveTextContent("최근 대화 메모는 꺼 두었어요");
    expect(screen.queryByText("날짜와 관계없이 최근 5개")).not.toBeInTheDocument();
    expect(screen.getByText("최근 판단 3건 (투자자 메모는 꺼 둠)")).toBeInTheDocument();
  });
});

describe("미장 메모 카드", () => {
  it("미장은 /api/us/memory… 를 쓰고, 만료 기준 날짜는 서버가 준 시장 날짜(뉴욕)", async () => {
    const fetchMock = mockFetch({
      "/api/us/memory/cards": {
        cards: [],
        settings: { use_recent_messages: true, recent_messages: 5 },
        today: "2026-11-03",
      },
    });
    renderWithProviders(<MemoPage />, { markets: BOTH_MARKETS, market: "US" });
    const date = await screen.findByLabelText("만료일 (선택) 날짜");
    expect(date).toHaveAttribute("min", "2026-11-03");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/us/memory/cards");
    expect(screen.getByPlaceholderText("종목명 또는 티커 (예: 애플, AAPL)")).toBeInTheDocument();
  });

  it("미장에서는 티커를 바로 적어도 종목코드로 받는다", () => {
    expect(looksLikeCode("aapl", "US")).toBe(true);
    expect(looksLikeCode("BRK.B", "US")).toBe(true);
    expect(looksLikeCode("005930", "US")).toBe(false);
    expect(looksLikeCode("AAPL")).toBe(false); // 국장은 6자리만
  });
});

describe("판단 주기 '기본값으로'", () => {
  it("되돌릴 기본값(시작~끝·주기)을 버튼 옆에 보여 준다", async () => {
    const info: ScheduleInfo = {
      market: "KR",
      timezone: "Asia/Seoul",
      tz_label: "한국 시각",
      custom: true,
      start: "08:00",
      end: "17:00",
      interval_minutes: 30,
      intervals: [30, 60],
      times: ["08:00", "08:30"],
      times_kst: ["08:00", "08:30"],
      market_hours: { open: "09:00", close: "15:30", order_cutoff: "15:20" },
      outside_market: ["08:00"],
      report_at: "15:45",
      next_runs: [],
      default: { start: "08:35", end: "15:15", interval_minutes: 60 },
      max_sessions: 96,
      day: "2026-11-05",
    };
    mockFetch({ "/api/schedule": info, "/api/us/schedule": { detail: "x" } });
    renderWithProviders(<ScheduleSettings />);
    const def = await screen.findByTestId("sched-default");
    expect(def).toHaveTextContent("기본값 08:35~15:15 · 1시간마다 (한국 시각)");
    const btn = within(def.parentElement!).getByRole("button", { name: "기본값으로" });
    await waitFor(() => expect(btn).toBeEnabled());
  });
});
