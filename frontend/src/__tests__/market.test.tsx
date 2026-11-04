import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { eventsUrl, isGlobalPath, marketApi, marketPath } from "../api/client";
import { Layout } from "../components/Layout";
import { EventsProvider } from "../events";
import { usePortfolio } from "../hooks";
import { useChatMessages } from "../live/hooks";
import { useCurrency, useMarket } from "../market";
import { isActive, NAV_GROUPS, tabIndex } from "../components/nav";
import { UsLlmProfileToggle } from "../pages/settings/LlmSection";
import { RiskSettings } from "../pages/settings/RiskSection";
import { money, price } from "../utils/format";
import { BOTH_MARKETS, KR_MARKET, mockFetch, renderWithProviders, SYSTEM, US_MARKET } from "./utils";

// Node 의 내장 localStorage 가 jsdom 것을 가리는 환경이 있어 메모리 저장소로 바꿔 끼운다
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

class FakeWS {
  static OPEN = 1;
  static instances: FakeWS[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send() {}
  close() {}
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => vi.unstubAllGlobals());

/** 두 시장 대화 수 (한눈에 보기 소식처럼 두 시장 대화를 구독하는 화면) */
function ChatCounts() {
  const kr = useChatMessages("KR");
  const us = useChatMessages("US");
  return (
    <div>
      <span data-testid="count-KR">{kr.messages.length}</span>
      <span data-testid="count-US">{us.messages.length}</span>
    </div>
  );
}

describe("시장별 주소", () => {
  it("미장이면 시장별 API 를 /api/us/… 로 바꾸고, 공통 API 는 그대로 둔다", () => {
    expect(marketPath("/api/portfolio", "US")).toBe("/api/us/portfolio");
    expect(marketPath("/api/agent/runs?limit=20", "US")).toBe("/api/us/agent/runs?limit=20");
    expect(marketPath("/api/market/AAPL/candles?days=180", "US")).toBe(
      "/api/us/market/AAPL/candles?days=180",
    );
    expect(marketPath("/api/market/search?q=app", "US")).toBe("/api/us/market/search?q=app");
    expect(marketPath("/api/schedule", "US")).toBe("/api/us/schedule");
    expect(marketPath("/api/engine/kill", "US")).toBe("/api/us/engine/kill");
    for (const p of [
      "/api/health",
      "/api/auth/status",
      "/api/push/subscribe",
      "/api/llm/profile",
      "/api/llm/costs?months=12",
      "/api/market/indicators",
      "/api/markets",
      "/api/overview",
      "/api/us/llm/profile",
      "/api/us/portfolio",
    ]) {
      expect(isGlobalPath(p)).toBe(true);
      expect(marketPath(p, "US")).toBe(p);
    }
    // '/api/market' 은 '/api/markets' 와 다르다
    expect(isGlobalPath("/api/market/universe")).toBe(false);
  });

  it("국장은 주소를 바꾸지 않고, 시장 API 묶음은 쿼리 키에 시장을 붙인다", async () => {
    expect(marketPath("/api/portfolio", "KR")).toBe("/api/portfolio");
    expect(marketApi("US").key("portfolio")).toEqual(["US", "portfolio"]);
    expect(marketApi("US")).toBe(marketApi("US")); // 시장마다 한 번만 만든다
    const fetchMock = mockFetch({ "/api/us/orders": [] });
    await marketApi("US").get("/api/orders?limit=3");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/us/orders?limit=3");
  });

  it("실시간 이벤트 주소: 국장 /ws/events · 미장 ?market=US (토큰도 함께)", () => {
    const loc = { protocol: "https:", host: "app.test" };
    expect(eventsUrl("KR", null, loc)).toBe("wss://app.test/ws/events");
    expect(eventsUrl("US", null, loc)).toBe("wss://app.test/ws/events?market=US");
    expect(eventsUrl("KR", "t k", { protocol: "http:", host: "h" })).toBe("ws://h/ws/events?token=t+k");
    expect(eventsUrl("US", "tok", loc)).toBe("wss://app.test/ws/events?market=US&token=tok");
  });

  it("미장이 켜져 있으면 두 시장 웹소켓을 함께 열고 각자 대화를 받는다", async () => {
    const fetchMock = mockFetch({
      "/api/us/chat": [],
      "/api/us/agent/progress": { active: false, typing: null, replying: false },
      "/api/us/events": [],
      "/api/chat": [],
      "/api/agent/progress": { active: false, typing: null, replying: false },
      "/api/events": [],
    });
    // 대화 기록은 대화를 보여 주는 화면이 있을 때만 받는다 (화면 1단계 F3) — 두 시장 소식을 보여 주는 화면처럼
    renderWithProviders(
      <EventsProvider>
        <ChatCounts />
      </EventsProvider>,
      { markets: BOTH_MARKETS },
    );
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    const urls = FakeWS.instances.map((w) => new URL(w.url).pathname + new URL(w.url).search);
    expect(urls).toEqual(["/ws/events", "/ws/events?market=US"]);
    const called = () => fetchMock.mock.calls.map(([u]) => String(u));
    await waitFor(() => expect(called()).toContain("/api/chat?limit=200"));
    await waitFor(() => expect(called()).toContain("/api/us/chat?limit=200"));
    // 각자 대화: 미장 웹소켓으로 온 메시지는 미장에만
    const us = FakeWS.instances[1];
    act(() =>
      us.onmessage?.({
        data: JSON.stringify({
          channel: "chat",
          data: {
            id: 9,
            ts: "2026-11-04T23:40:00+09:00",
            role: "system",
            kind: "text",
            content: "미장",
            run_id: null,
            payload: {},
          },
        }),
      }),
    );
    expect(screen.getByTestId("count-US")).toHaveTextContent("1");
    expect(screen.getByTestId("count-KR")).toHaveTextContent("0");
  });

  it("미장이 꺼져 있으면 국장 웹소켓 하나만", async () => {
    mockFetch({});
    renderWithProviders(
      <EventsProvider>
        <div />
      </EventsProvider>,
      { markets: [KR_MARKET, { ...US_MARKET, enabled: false }] },
    );
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    expect(FakeWS.instances[0].url).toMatch(/\/ws\/events$/);
  });
});

describe("통화 표시", () => {
  it("금액: 국장 원 · 미장 달러(센트까지), 손익 부호", () => {
    expect(money(1234567, "KRW")).toBe("1,234,567원");
    expect(money(1234.5, "USD")).toBe("$1,234.50");
    expect(money(-12.345, "USD")).toBe("-$12.35");
    expect(money(-0.001, "USD")).toBe("$0.00");
    expect(money(108724, "KRW", { sign: true })).toBe("+108,724원");
    expect(money(3.2, "USD", { sign: true })).toBe("+$3.20");
    expect(money(0, "USD", { sign: true })).toBe("$0.00");
    expect(money(null, "USD")).toBe("—");
  });

  it("주가: 국장은 원 단위 숫자 · 미장은 $ 2자리, $1 미만은 4자리", () => {
    expect(price(154700, "KRW")).toBe("154,700");
    expect(price(209395.25, "KRW", 2)).toBe("209,395.25");
    expect(price(337.5, "USD")).toBe("$337.50");
    expect(price(0.12345, "USD")).toBe("$0.1235");
    expect(price(undefined, "USD")).toBe("—");
  });
});

/** 지금 시장의 계좌를 보여 주고 시장을 바꾸는 작은 화면 */
function Probe() {
  const { data } = usePortfolio();
  const cur = useCurrency();
  const { view, setView } = useMarket();
  return (
    <div>
      <span data-testid="view">{view}</span>
      <span data-testid="equity">{money(data?.equity, cur)}</span>
      <button onClick={() => setView("US")}>to-us</button>
      <button onClick={() => setView("KR")}>to-kr</button>
    </div>
  );
}

const PF = (equity: number) => ({ equity, positions: [] });

describe("시장 전환과 캐시 분리", () => {
  it("시장마다 캐시가 따로라 한 시장의 숫자가 다른 시장 화면에 보이지 않는다", async () => {
    let usResolve: (v: unknown) => void = () => {};
    const fetchMock = mockFetch({
      "/api/us/portfolio": () => new Promise((r) => (usResolve = r)),
      "/api/portfolio": PF(9_984_359),
    });
    const { qc } = renderWithProviders(<Probe />, { markets: BOTH_MARKETS, market: "KR" });
    await waitFor(() => expect(screen.getByTestId("equity")).toHaveTextContent("9,984,359원"));

    fireEvent.click(screen.getByText("to-us"));
    // 미장 응답이 오기 전에는 비어 있다 — 국장 숫자를 달러로 보여 주지 않는다
    expect(screen.getByTestId("view")).toHaveTextContent("US");
    expect(screen.getByTestId("equity")).toHaveTextContent("—");
    await act(async () => usResolve(PF(10_000)));
    await waitFor(() => expect(screen.getByTestId("equity")).toHaveTextContent("$10,000.00"));

    expect(qc.getQueryData(["KR", "portfolio"])).toEqual(PF(9_984_359));
    expect(qc.getQueryData(["US", "portfolio"])).toEqual(PF(10_000));
    // 국장으로 돌아가면 캐시에서 바로 (다시 받는 동안에도 국장 숫자)
    fireEvent.click(screen.getByText("to-kr"));
    expect(screen.getByTestId("equity")).toHaveTextContent("9,984,359원");
    expect(fetchMock.mock.calls.filter(([u]) => u === "/api/us/portfolio")).toHaveLength(1);
  });

  it("고른 시장을 저장하고, ?m= 딥 링크로도 연다", async () => {
    mockFetch({ "/api/portfolio": PF(1), "/api/us/portfolio": PF(2) });
    const first = renderWithProviders(<Probe />, { markets: BOTH_MARKETS });
    // 처음이면 '전체'
    expect(screen.getByTestId("view")).toHaveTextContent("ALL");
    fireEvent.click(screen.getByText("to-us"));
    expect(window.localStorage.getItem("market.view")).toBe("US");
    expect(window.localStorage.getItem("market.last")).toBe("US");
    first.unmount();

    // 다시 열면 저장된 미장
    const second = renderWithProviders(<Probe />, { markets: BOTH_MARKETS });
    expect(screen.getByTestId("view")).toHaveTextContent("US");
    second.unmount();

    // ?m=kr 딥 링크가 저장값보다 먼저 — 적용하고 주소에서는 지운다
    function Where() {
      const { search } = useLocation();
      return <span data-testid="search">{search}</span>;
    }
    renderWithProviders(
      <>
        <Probe />
        <Where />
      </>,
      { markets: BOTH_MARKETS, route: "/?m=kr&x=1" },
    );
    expect(screen.getByTestId("view")).toHaveTextContent("KR");
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent("?x=1"));
    expect(window.localStorage.getItem("market.view")).toBe("KR");
  });

  it("미장이 꺼져 있으면 저장값이 미장이어도 국장만", () => {
    window.localStorage.setItem("market.view", "US");
    mockFetch({ "/api/portfolio": PF(1) });
    renderWithProviders(<Probe />, { markets: [KR_MARKET, { ...US_MARKET, enabled: false }] });
    expect(screen.getByTestId("view")).toHaveTextContent("KR");
  });
});

function layoutRoutes() {
  function Here() {
    const { pathname } = useLocation();
    const { view, market } = useMarket();
    return <span data-testid="here">{`${pathname} ${view} ${market}`}</span>;
  }
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Here />} />
        <Route path="room" element={<Here />} />
        <Route path="costs" element={<Here />} />
      </Route>
    </Routes>
  );
}

const SYS_US = {
  ...SYSTEM,
  market: "US",
  currency: "USD",
  disclaimer: "미장 모의투자(PAPER, 달러 가상계좌)입니다.",
};

describe("상단 시장 전환 (전체 · 국장 · 미장)", () => {
  it("세 칸과 장 상태 점, 미장 '모의' 표시를 보여 주고 고르면 그 시장이 된다", async () => {
    mockFetch({ "/api/us/system": SYS_US, "/api/system": SYSTEM });
    renderWithProviders(layoutRoutes(), { markets: BOTH_MARKETS, market: "ALL" });
    const sw = screen.getByRole("group", { name: "시장 보기" });
    const [all, kr, us] = within(sw).getAllByRole("button");
    expect(all).toHaveAccessibleName("전체");
    expect(kr).toHaveAccessibleName("국장 · 장중");
    expect(us).toHaveAccessibleName("미장 · 장 마감 · 모의투자");
    expect(within(us).getByText("모의")).toBeInTheDocument();
    expect(kr.querySelector(".mkt-dot.open")).not.toBeNull();
    expect(all).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("here")).toHaveTextContent("/ ALL KR");

    fireEvent.click(us);
    expect(us).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("here")).toHaveTextContent("/ US US");
    // 상단 바는 미장 상태 (엔진·면책 문구)
    await waitFor(() => expect(screen.getByText(/달러 가상계좌/)).toBeInTheDocument());
  });

  it("미장이 꺼져 있으면 전환을 그리지 않는다", () => {
    mockFetch({ "/api/system": SYSTEM });
    renderWithProviders(layoutRoutes(), { markets: [KR_MARKET, { ...US_MARKET, enabled: false }] });
    expect(screen.queryByRole("group", { name: "시장 보기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /미장/ })).not.toBeInTheDocument();
  });

  it("'전체'에서 시장 화면을 열면 마지막 시장으로 보여 주고, '전체'를 누르면 한눈에 보기로 돌아간다", async () => {
    mockFetch({ "/api/us/system": SYS_US, "/api/system": SYSTEM });
    window.localStorage.setItem("market.last", "US");
    renderWithProviders(layoutRoutes(), { markets: BOTH_MARKETS, market: "ALL", route: "/room" });
    const sw = screen.getByRole("group", { name: "시장 보기" });
    // 트레이딩 룸은 미장(마지막 시장) — 전환 버튼도 지금 보이는 미장을 가리킨다
    expect(screen.getByTestId("here")).toHaveTextContent("/room US US");
    expect(within(sw).getByRole("button", { name: /^미장/ })).toHaveAttribute("aria-pressed", "true");
    // 고른 값('전체')은 그대로
    expect(window.localStorage.getItem("market.view")).toBeNull();

    fireEvent.click(within(sw).getByRole("button", { name: "전체" }));
    expect(screen.getByTestId("here")).toHaveTextContent("/ ALL US");
    // LLM 비용처럼 시장과 관계없는 화면은 '전체' 그대로 — 휴대폰에서는 '더보기' 아래
    expect(tabIndex("/costs")).toBe(4);
  });

  it("전체 보기의 킬 스위치는 두 시장을 함께 멈춘다", async () => {
    const fetchMock = mockFetch({
      "/api/us/system": SYS_US,
      "/api/system": SYSTEM,
      "/api/engine/kill": { status: "KILLED", canceled: 0 },
      "/api/us/engine/kill": { status: "KILLED", canceled: 0 },
    });
    renderWithProviders(layoutRoutes(), { markets: BOTH_MARKETS, market: "ALL" });
    await waitFor(() => expect(screen.getByTestId("engine-status")).toHaveTextContent("엔진 실행 중"));
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    const dialog = screen.getByRole("dialog", { name: /킬 스위치를 작동할까요/ });
    expect(dialog).toHaveTextContent("국장·미장 자동매매를 즉시 멈추고");
    fireEvent.click(within(dialog).getByRole("button", { name: "즉시 전체 정지" }));
    await waitFor(() => {
      const posts = fetchMock.mock.calls.filter(([, i]) => (i as RequestInit | undefined)?.method === "POST");
      expect(posts.map(([u]) => u).sort()).toEqual(["/api/engine/kill", "/api/us/engine/kill"]);
    });
  });
});

describe("모바일 탭 위치 (시장)", () => {
  it("메모 카드는 '매니저' 메뉴(휴대폰은 더보기), 한눈에 보기('/')는 '홈' 탭", () => {
    const manager = NAV_GROUPS.flatMap((g) => g.items).find((n) => n.to === "/agent")!;
    expect(isActive(manager, "/memo")).toBe(true);
    expect(tabIndex("/memo")).toBe(4);
    expect(tabIndex("/")).toBe(0);
    expect(tabIndex("/stock/AAPL")).toBe(2);
  });
});

describe("미장 설정", () => {
  it("리스크 한도의 금액 칸은 미장이면 달러로 묻는다", async () => {
    mockFetch({
      "/api/us/risk": {
        config: {
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
      },
    });
    renderWithProviders(<RiskSettings />, { markets: BOTH_MARKETS, market: "US" });
    const input = await screen.findByLabelText("1회 주문 최대 금액(달러)");
    await waitFor(() => expect(input).toHaveValue(1500));
  });

  it("미장 매니저 LLM: 유료는 확인을 거쳐 /api/us/llm/profile 로 바꾼다", async () => {
    const profile = (active: "free" | "paid") => ({
      active,
      market: "US",
      profiles: {
        free: { label: "무료", env: "LLM_TEST_*", configured: true, model: "qwen", api: "chat" },
        paid: { label: "유료", env: "LLM_*", configured: true, model: "gpt-6.1-terra", api: "responses" },
      },
    });
    let active: "free" | "paid" = "free";
    const fetchMock = mockFetch({
      "/api/us/llm/profile": (_u: string, init?: RequestInit) => {
        if (init?.method === "PUT") active = JSON.parse(String(init.body)).active;
        return profile(active);
      },
      "/api/us/system": SYS_US,
    });
    renderWithProviders(<UsLlmProfileToggle />, { markets: BOTH_MARKETS, market: "KR" });
    const group = await screen.findByRole("group", { name: "미장 매니저 LLM" });
    expect(within(group).getByRole("button", { name: "무료" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(group).getByRole("button", { name: "유료" }));
    const dialog = screen.getByRole("dialog", { name: /미장 매니저를 유료 모델로 바꿀까요/ });
    expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "PUT")).toBe(
      false,
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "유료로 바꾸기" }));
    await waitFor(() =>
      expect(within(group).getByRole("button", { name: "유료" })).toHaveAttribute("aria-pressed", "true"),
    );
    const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT")!;
    expect(put[0]).toBe("/api/us/llm/profile");
    expect(JSON.parse(String((put[1] as RequestInit).body))).toEqual({ active: "paid" });
  });
});
