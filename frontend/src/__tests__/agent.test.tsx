import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentPage } from "../pages/Agent";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const STRUCTURE = {
  persona: { name: "알파", emoji: "🦉" },
  llm: {
    configured: true,
    model: "test-model",
    profile: "paid",
    mode: "auto",
    max_steps: 24,
    max_tokens: 8192,
  },
  sources: [
    { name: "토스증권", ready: true },
    { name: "DART", ready: true },
    { name: "NAVER", ready: false, note: "키 인증 실패" },
    { name: "모의계좌", ready: true },
  ],
  tools: [
    { name: "get_portfolio", description: "계좌", group: "portfolio", source: "모의계좌", ready: true },
    { name: "get_supply_demand", description: "수급", group: "market", source: "토스증권", ready: true },
    { name: "search_news", description: "뉴스", group: "research", source: "NAVER", ready: false },
    { name: "submit_decisions", description: "제출", group: "decision", source: "모의계좌", ready: true },
  ],
  schedule: {
    interval_minutes: 15,
    morning: "08:35",
    sessions: ["09:15", "09:30", "15:15"],
    report: "15:45",
    monitor_minutes: 1,
    refresh_minutes: 10,
  },
  risk: {
    max_position_weight: 0.2,
    max_order_amount: 2_000_000,
    daily_loss_limit: -0.03,
    stop_loss: -0.05,
    take_profit: 0.1,
    min_order_interval_minutes: 60,
    max_positions: 10,
    min_cash_ratio: 0.05,
    allow_new_buys: true,
  },
  paper: { initial_cash: 10_000_000, commission_rate: 0.00015, sell_tax_rate: 0.002, slippage_bps: 5 },
  trading_mode: "paper",
  live_daily_buy_limit: null,
  engine_status: "RUNNING",
  agent_running: true,
};

describe("에이전트 구조", () => {
  it("실제 설정값으로 워크플로와 도구를 그린다", async () => {
    mockFetch({ "/api/agent/structure": STRUCTURE });
    renderWithProviders(<AgentPage />);
    expect(await screen.findByText("알파 매니저")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem", { hidden: false }).length).toBeGreaterThan(7);
    expect(screen.getByText(/15분마다 \(09:15~15:15, 3회\)/)).toBeInTheDocument();
    expect(screen.getByText("2,000,000원")).toBeInTheDocument();
    expect(screen.getByText("get_supply_demand")).toBeInTheDocument();
    expect(screen.getByText("NAVER · 연결 안 됨")).toBeInTheDocument();
    expect(screen.getByText(/○ NAVER · 키 인증 실패/)).toBeInTheDocument(); // 키가 틀리면 이유까지
    expect(screen.getByText("진행 중")).toBeInTheDocument(); // 판단 중이면 리서치 단계 강조
  });
});

describe("에이전트 구조 (실거래)", () => {
  it("실거래 모드면 실주문 단계와 매수 상한을 보여준다", async () => {
    mockFetch({
      "/api/agent/structure": { ...STRUCTURE, trading_mode: "live", live_daily_buy_limit: 5_000_000 },
    });
    renderWithProviders(<AgentPage />);
    expect(await screen.findByText("토스증권 실주문")).toBeInTheDocument();
    expect(screen.getByText(/하루 매수 총액 상한 5,000,000원/)).toBeInTheDocument();
    expect(screen.queryByText("모의 체결")).not.toBeInTheDocument();
  });
});
