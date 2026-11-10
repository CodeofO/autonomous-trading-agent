import { screen, within } from "@testing-library/react";
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

const MEMORY = {
  rules: [
    {
      name: "최근 판단 세션",
      where: "판단 세션·답장",
      detail: "완료된 세션 최근 3개의 요약(160자)과 사고판 종목",
    },
    {
      name: "대화에서 되풀이한 관심사",
      where: "판단 세션·답장",
      detail: "여러 날 되풀이해 물으면 중요도가 오르고, 한 번 묻고 만 주제는 며칠 안에 빠진다",
    },
    { name: "오늘 대화", where: "답장", detail: "오늘(한국 시각) 트레이딩 룸 대화 최근 30개" },
    { name: "계좌·매매 내역", where: "판단 세션·답장", detail: "기억이 아니라 매번 새로 조회" },
  ],
  sessions: [
    { run_id: 42, at: "11/04 10:15", summary: "반도체 비중 유지", actions: "삼성전자 BUY" },
    { run_id: 41, at: "11/04 10:00", summary: "관망", actions: "거래 없음" },
  ],
  topics: [
    {
      id: 7,
      key: "sym:035720",
      label: "카카오(035720)",
      symbol: "035720",
      score: 0.62,
      included: true,
      pin: "" as const,
      days: 3,
      streak: 2,
      mentions: 4,
      last_day: "2026-11-04",
      quotes: [{ id: 70, at: "2026-11-04T08:00:00+09:00", text: "카카오 실적 어때?" }],
      llm_level: 1,
      llm_reason: "여러 날 물음",
      forgotten: false,
    },
  ],
  use_topics: true,
};

const memoryCard = () => screen.findByRole("region", { name: "매니저의 기억" });

describe("매니저의 기억", () => {
  it("규칙과 지금 기억하는 판단 세션·대화 주제를 보여 준다", async () => {
    mockFetch({ "/api/agent/structure": { ...STRUCTURE, memory: MEMORY } });
    renderWithProviders(<AgentPage />);
    const card = await memoryCard();
    expect(card).toHaveTextContent(
      "별도 장기 기억 저장소는 없고, 판단·답장 때마다 아래 내용을 DB에서 다시 읽어 넣어요",
    );

    const rules = within(card).getByRole("list", { name: "기억 규칙" });
    const rows = within(rules).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(rows[2]).toHaveTextContent("오늘 대화");
    expect(rows[2]).toHaveTextContent("답장");
    expect(rows[2]).toHaveTextContent("오늘(한국 시각) 트레이딩 룸 대화 최근 30개");

    const sessions = within(within(card).getByRole("list", { name: "최근 판단 세션" })).getAllByRole(
      "listitem",
    );
    expect(sessions).toHaveLength(2);
    expect(sessions[0]).toHaveTextContent("세션 #42");
    expect(sessions[0]).toHaveTextContent("11/04 10:15");
    expect(sessions[0]).toHaveTextContent("반도체 비중 유지");
    expect(sessions[0]).toHaveTextContent("매매: 삼성전자 BUY");

    const topics = within(within(card).getByRole("list", { name: "대화에서 되풀이한 관심사" })).getAllByRole(
      "listitem",
    );
    expect(topics).toHaveLength(1);
    expect(topics[0]).toHaveTextContent("카카오(035720)");
    expect(topics[0]).toHaveTextContent("중요도 62 · 3일 언급");
    expect(topics[0]).toHaveTextContent("카카오 실적 어때?");
    expect(within(card).getByRole("link", { name: "주제 보기·고정 →" })).toHaveAttribute("href", "/memo");
  });

  it("기억할 세션·주제가 없으면 빈 안내를 보여 준다", async () => {
    mockFetch({ "/api/agent/structure": { ...STRUCTURE, memory: { ...MEMORY, sessions: [], topics: [] } } });
    renderWithProviders(<AgentPage />);
    const card = await memoryCard();
    expect(within(card).getByText("아직 완료된 판단 세션이 없어요.")).toBeInTheDocument();
    expect(within(card).getByText("지금 기억에 들어가는 주제가 없어요.")).toBeInTheDocument();
    expect(within(card).queryByRole("list", { name: "최근 판단 세션" })).not.toBeInTheDocument();
  });

  it("메모 카드 관리 링크가 있고, 켜진 메모 카드를 짧게 보여 준다", async () => {
    mockFetch({
      "/api/agent/structure": {
        ...STRUCTURE,
        memory: {
          ...MEMORY,
          cards: [
            { kind: "focus", symbol: "000660", name: "SK하이닉스", text: "HBM 수주 확인되면 매수 검토" },
            { kind: "note", symbol: null, name: null, text: "현금 비중 30% 이상" },
          ],
        },
      },
    });
    renderWithProviders(<AgentPage />);
    const card = await memoryCard();
    expect(within(card).getByRole("link", { name: "메모 카드 관리 →" })).toHaveAttribute("href", "/memo");
    const rows = within(within(card).getByRole("list", { name: "켜진 메모 카드" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("주목할 종목");
    expect(rows[0]).toHaveTextContent("SK하이닉스 000660");
    expect(rows[0]).toHaveTextContent("HBM 수주 확인되면 매수 검토");
    expect(rows[1]).toHaveTextContent("운용 지시");
    expect(rows[1]).toHaveTextContent("현금 비중 30% 이상");
  });

  it("켜진 메모 카드가 없으면 만들기 링크를, 예전 서버(cards 없음)면 목록 자체를 숨긴다", async () => {
    mockFetch({ "/api/agent/structure": { ...STRUCTURE, memory: { ...MEMORY, cards: [] } } });
    const { unmount } = renderWithProviders(<AgentPage />);
    let card = await memoryCard();
    expect(within(card).getByText(/켜진 메모 카드가 없어요/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "메모 카드 만들기 →" })).toHaveAttribute("href", "/memo");
    unmount();
    vi.unstubAllGlobals();

    mockFetch({ "/api/agent/structure": { ...STRUCTURE, memory: MEMORY } });
    renderWithProviders(<AgentPage />);
    card = await memoryCard();
    expect(within(card).queryByText("켜진 메모 카드")).not.toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "메모 카드 관리 →" })).toBeInTheDocument();
  });

  it("예전 서버(memory 없음)면 카드를 그리지 않는다", async () => {
    mockFetch({ "/api/agent/structure": STRUCTURE });
    renderWithProviders(<AgentPage />);
    expect(await screen.findByText("알파 매니저")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "매니저의 기억" })).not.toBeInTheDocument();
  });
});
