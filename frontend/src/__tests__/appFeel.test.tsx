import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import type { Position } from "../api/types";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { ChatFeed } from "../components/chat/ChatFeed";
import { BANNER_DELAY_MS, ConnectionBanner } from "../components/ConnectionBanner";
import { HoldingsTable } from "../components/HoldingsTable";
import { KillSwitch } from "../components/KillSwitch";
import { Layout } from "../components/Layout";
import { StockSheetHost } from "../components/Overlays";
import { RelTime } from "../components/RelTime";
import { EventsProvider } from "../events";
import { Logs } from "../pages/Logs";
import { TradingRoom } from "../pages/TradingRoom";
import { toast } from "../ui/toast";
import { fmtAgo } from "../utils/format";
import { BOTH_MARKETS, KR_MARKET, mockFetch, renderWithProviders, SYSTEM } from "./utils";

// 화면 2단계: 앱 같은 화면 — 자리 표시·토스트·연결 끊김 띠·⌘K·아래 시트·당겨서 새로고침·살아 있는 숫자

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
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}
const krWs = () => FakeWS.instances.filter((w) => !w.url.includes("market=US")).at(-1)!;
const usWs = () => FakeWS.instances.filter((w) => w.url.includes("market=US")).at(-1)!;

/** 화면 조건 흉내: 휴대폰 폭 · 동작 줄이기 */
function setMedia({ mobile = false, reduce = false }: { mobile?: boolean; reduce?: boolean }) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: (mobile && query.includes("max-width")) || (reduce && query.includes("reduced-motion")),
      media: query,
    })),
  );
}

function Where() {
  const { pathname } = useLocation();
  return <span data-testid="where">{pathname}</span>;
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("sheet-open");
});

describe("자리 표시 — 받는 동안 '없어요'를 보여 주지 않는다", () => {
  it("트레이딩 룸: 채팅 기록을 받기 전에는 말풍선 자리, 받은 뒤 비어 있으면 '아직 조용하네요'", async () => {
    let release: (v: unknown) => void = () => {};
    mockFetch({
      "/api/chat": () => new Promise((r) => (release = r)),
      "/api/agent/progress": { active: false, typing: null, replying: false },
      "/api/agent/runs": [],
      "/api/system": SYSTEM,
    });
    renderWithProviders(
      <EventsProvider>
        <TradingRoom />
      </EventsProvider>,
    );
    // 웹소켓이 열리기 전(최대 300ms)과 기록을 받는 동안 모두 자리 표시
    expect(screen.getByRole("status", { name: "대화 불러오는 중" })).toBeInTheDocument();
    expect(screen.queryByText(/아직 조용하네요/)).not.toBeInTheDocument();
    act(() => krWs().open());
    expect(screen.getByRole("status", { name: "대화 불러오는 중" })).toBeInTheDocument();
    expect(screen.queryByText(/아직 조용하네요/)).not.toBeInTheDocument();
    // 세션 목록도 받는 동안 자리 표시 (빈 목록 대신)
    await act(async () => release([]));
    expect(await screen.findByText(/아직 조용하네요/)).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "대화 불러오는 중" })).not.toBeInTheDocument();
  });

  it("채팅 상자: loading 이면 비어 있어도 안내 대신 자리 표시", () => {
    const persona = { name: "알파", emoji: "🦉" };
    const { rerender } = renderWithProviders(
      <ChatFeed messages={[]} typing={null} persona={persona} showInput={false} loading />,
    );
    expect(screen.getByRole("status", { name: "대화 불러오는 중" })).toBeInTheDocument();
    expect(screen.queryByText(/아직 조용하네요/)).not.toBeInTheDocument();
    rerender(<ChatFeed messages={[]} typing={null} persona={persona} showInput={false} />);
    expect(screen.getByText(/아직 조용하네요/)).toBeInTheDocument();
  });

  it("이벤트 로그: 받기 전에는 줄 자리, 받은 뒤 비어 있으면 안내와 '모든 유형 보기'", async () => {
    let release: (v: unknown) => void = () => {};
    mockFetch({ "/api/events": () => new Promise((r) => (release = r)) });
    renderWithProviders(
      <EventsProvider>
        <Logs />
      </EventsProvider>,
    );
    act(() => krWs().open());
    expect(screen.getByRole("status", { name: "이벤트 로그 불러오는 중" })).toBeInTheDocument();
    expect(screen.queryByText("아직 기록이 없어요.")).not.toBeInTheDocument();
    await act(async () => release([]));
    expect(await screen.findByText("아직 기록이 없어요.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("이벤트 유형"), { target: { value: "FILL" } });
    expect(screen.getByText("FILL 기록이 아직 없어요.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "모든 유형 보기" }));
    expect(screen.getByLabelText("이벤트 유형")).toHaveValue("ALL");
  });
});

describe("토스트 — 버튼을 누른 결과", () => {
  it("킬 스위치: 정지하면 '멈췄어요 · 미체결 N건 취소', 재개하면 '다시 켰어요' (읽기 프로그램이 읽는 자리)", async () => {
    mockFetch({
      "/api/engine/kill": { status: "KILLED", canceled: 2 },
      "/api/engine/resume": { status: "RUNNING" },
    });
    const view = renderWithProviders(<KillSwitch status="RUNNING" />);
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    fireEvent.click(screen.getByRole("button", { name: "즉시 전체 정지" }));
    const done = await screen.findByText("⏻ 자동매매를 멈췄어요 · 미체결 주문 2건 취소");
    expect(done).toHaveClass("toast", "ok");
    expect(done.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");

    view.rerender(<KillSwitch status="KILLED" />);
    fireEvent.click(screen.getByRole("button", { name: /자동매매 재개/ }));
    expect(await screen.findByText("▶ 자동매매를 다시 켰어요")).toBeInTheDocument();
  });

  it("재개에 실패하면 이유를 오류 토스트로 (바로 읽는 자리)", async () => {
    mockFetch({
      "/api/engine/resume": () =>
        new Response(JSON.stringify({ detail: "엔진이 아직 정지 중이에요" }), { status: 409 }),
    });
    renderWithProviders(<KillSwitch status="KILLED" />);
    fireEvent.click(screen.getByRole("button", { name: /자동매매 재개/ }));
    const err = await screen.findByText("재개하지 못했어요: 엔진이 아직 정지 중이에요");
    expect(err).toHaveClass("error");
    expect(err.closest("[aria-live]")).toHaveAttribute("aria-live", "assertive");
  });

  it("누르거나 닫기를 누르면 사라지고, 같은 글자는 쌓지 않고 3개까지만", () => {
    renderWithProviders(<div />);
    act(() => {
      toast("하나");
      toast("하나");
    });
    expect(screen.getAllByText("하나")).toHaveLength(1);
    act(() => {
      toast("둘");
      toast.info("셋");
      toast("넷");
    });
    expect(screen.getAllByTestId("toast").map((t) => t.firstChild?.textContent)).toEqual(["둘", "셋", "넷"]);
    fireEvent.click(screen.getByText("셋"));
    expect(screen.queryByText("셋")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByText("넷")).getByRole("button", { name: "알림 닫기" }));
    expect(screen.queryByText("넷")).not.toBeInTheDocument();
  });

  it("저절로 사라진다", () => {
    vi.useFakeTimers();
    renderWithProviders(<div />);
    act(() => void toast("잠깐"));
    expect(screen.getByText("잠깐")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.queryByText("잠깐")).not.toBeInTheDocument();
  });
});

describe("실시간 연결 끊김 띠", () => {
  it("끊긴 채로 잠시 지나면 띠를 보이고, 다시 연결되면 바로 감춘다", () => {
    vi.useFakeTimers();
    mockFetch({});
    renderWithProviders(
      <EventsProvider>
        <ConnectionBanner />
      </EventsProvider>,
    );
    // 처음 연결하는 잠깐 동안은 보이지 않는다
    act(() => vi.advanceTimersByTime(BANNER_DELAY_MS - 100));
    expect(screen.queryByTestId("conn-banner")).not.toBeInTheDocument();
    act(() => krWs().open());
    act(() => vi.advanceTimersByTime(BANNER_DELAY_MS * 2));
    expect(screen.queryByTestId("conn-banner")).not.toBeInTheDocument();

    act(() => krWs().drop());
    expect(screen.queryByTestId("conn-banner")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(BANNER_DELAY_MS));
    expect(screen.getByRole("status")).toHaveTextContent("실시간 연결이 끊겼어요 · 다시 연결 중…");

    // 다시 연결(새 웹소켓)되면 바로 사라진다
    act(() => vi.advanceTimersByTime(600));
    act(() => krWs().open());
    expect(screen.queryByTestId("conn-banner")).not.toBeInTheDocument();
  });

  it("전체 보기에서 한 시장만 끊기면 그 시장을 앞에 붙인다", () => {
    vi.useFakeTimers();
    mockFetch({});
    renderWithProviders(
      <EventsProvider>
        <ConnectionBanner />
      </EventsProvider>,
      { markets: BOTH_MARKETS, market: "ALL" },
    );
    act(() => {
      krWs().open();
      usWs().open();
    });
    act(() => usWs().drop());
    act(() => vi.advanceTimersByTime(BANNER_DELAY_MS));
    expect(screen.getByTestId("conn-banner")).toHaveTextContent("🇺🇸 미장 실시간 연결이 끊겼어요");
  });

  it("실시간 저장소 없이 그린 화면(테스트·로그인 전)에서는 띠가 없다", () => {
    vi.useFakeTimers();
    renderWithProviders(<ConnectionBanner />);
    act(() => vi.advanceTimersByTime(BANNER_DELAY_MS * 3));
    expect(screen.queryByTestId("conn-banner")).not.toBeInTheDocument();
  });
});

function layout(extra?: ReactNode) {
  return (
    <>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={extra ?? <div>홈 본문</div>} />
          <Route path="room" element={<div>룸 본문</div>} />
          <Route path="settings/:section?" element={<div>설정 본문</div>} />
          <Route path="stock/:symbol" element={<div>종목 본문</div>} />
        </Route>
      </Routes>
      <Where />
    </>
  );
}

const SEARCH = [
  { symbol: "005930", name: "삼성전자", market: "KOSPI" },
  { symbol: "009150", name: "삼성전기", market: "KOSPI" },
];

describe("⌘K 빠른 이동", () => {
  it("Ctrl+K 로 열고, 화면·설정 주제를 걸러 ↑/↓·Enter 로 이동, Esc 로 닫는다", async () => {
    mockFetch({ "/api/system": SYSTEM });
    renderWithProviders(layout());
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: "빠른 이동" });
    const input = within(dialog).getByRole("combobox", { name: "빠른 이동 · 종목 검색" });
    await waitFor(() => expect(input).toHaveFocus());
    // 처음에는 화면 목록 (첫 줄이 골라져 있다)
    const first = within(dialog).getAllByRole("option")[0];
    expect(first).toHaveTextContent("홈");
    expect(first).toHaveAttribute("aria-selected", "true");

    fireEvent.change(input, { target: { value: "리스크" } });
    const opts = within(dialog).getAllByRole("option");
    expect(opts.map((o) => o.textContent)).toEqual([expect.stringContaining("설정 › 리스크·주문")]);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("where")).toHaveTextContent("/settings/risk");
    expect(screen.queryByRole("dialog", { name: "빠른 이동" })).not.toBeInTheDocument();

    // 다시 열어 ↓ 로 옮기고 Esc 로 닫기
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const again = await screen.findByRole("dialog", { name: "빠른 이동" });
    const box = within(again).getByRole("combobox");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(within(again).getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", within(again).getAllByRole("option")[1].id);
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "빠른 이동" })).not.toBeInTheDocument();
  });

  it("종목 검색은 지금 시장 API 로 (국장 /api/market/search) — 고르면 종목 화면", async () => {
    const fetchMock = mockFetch({ "/api/system": SYSTEM, "/api/market/search": SEARCH });
    renderWithProviders(layout());
    fireEvent.click(screen.getByRole("button", { name: "빠른 이동 · 종목 검색" }));
    const dialog = await screen.findByRole("dialog", { name: "빠른 이동" });
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "삼성" } });
    const hit = await within(dialog).findByRole("option", { name: /삼성전자/ });
    expect(within(dialog).getByText("🇰🇷 국장 종목")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/market/search"))!;
    expect(call[0]).toBe("/api/market/search?q=%EC%82%BC%EC%84%B1&limit=20");
    fireEvent.click(hit);
    expect(screen.getByTestId("where")).toHaveTextContent("/stock/005930");
  });

  it("미장을 보고 있으면 /api/us/market/search 로 찾는다", async () => {
    const fetchMock = mockFetch({
      "/api/us/system": { ...SYSTEM, market: "US" },
      "/api/system": SYSTEM,
      "/api/us/market/search": [{ symbol: "AAPL", name: "애플", market: "NASDAQ" }],
    });
    renderWithProviders(layout(), { markets: BOTH_MARKETS, market: "US" });
    fireEvent.keyDown(window, { key: "K", metaKey: true });
    const dialog = await screen.findByRole("dialog", { name: "빠른 이동" });
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "aapl" } });
    expect(await within(dialog).findByRole("option", { name: /애플/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/us/market/search?q=aapl&limit=20")).toBe(
      true,
    );
  });

  it("시장 바꾸기: '전체 보기'를 고르면 시장 화면에서 한눈에 보기로", async () => {
    mockFetch({ "/api/system": SYSTEM, "/api/us/system": { ...SYSTEM, market: "US" } });
    renderWithProviders(layout(), { markets: BOTH_MARKETS, market: "KR", route: "/room" });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: "빠른 이동" });
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "전체" } });
    fireEvent.click(within(dialog).getByRole("option", { name: /전체 보기/ }));
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/);
    const sw = screen.getByRole("group", { name: "시장 보기" });
    expect(within(sw).getByRole("button", { name: "전체" })).toHaveAttribute("aria-pressed", "true");
  });
});

const POS: Position = {
  symbol: "005930",
  name: "삼성전자",
  quantity: 10,
  avg_price: 70000,
  last_price: 90500,
  price_day: "2026-11-04",
  market_value: 905000,
  unrealized_pnl: 205000,
  pnl_pct: 29.29,
  weight_pct: 9.1,
  holding_days: 3,
};
const BARS = [
  { time: "2026-11-03", open: 1, high: 1, low: 1, close: 90300, volume: 1 },
  { time: "2026-11-04", open: 1, high: 1, low: 1, close: 90500, volume: 1 },
];

describe("아래 시트 (휴대폰)", () => {
  it("휴대폰의 확인 창은 아래 시트 — 버튼이 엄지 쪽, Esc·바깥을 누르면 닫힌다", async () => {
    setMedia({ mobile: true });
    const fetchMock = mockFetch({});
    renderWithProviders(<KillSwitch status="RUNNING" />);
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    const sheet = await screen.findByRole("dialog", { name: /킬 스위치를 작동할까요/ });
    expect(sheet).toHaveClass("sheet");
    expect(sheet.querySelector(".sheet-actions")).toContainElement(
      within(sheet).getByRole("button", { name: "즉시 전체 정지" }),
    );
    expect(document.documentElement).toHaveClass("sheet-open");
    // 열리면 시트 안 첫 버튼으로 초점
    await waitFor(() => expect(within(sheet).getByRole("button", { name: "취소" })).toHaveFocus());

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.documentElement).not.toHaveClass("sheet-open");

    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    const again = await screen.findByRole("dialog", { name: /킬 스위치를 작동할까요/ });
    fireEvent.click(again.parentElement!); // 어두운 바깥
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("넓은 화면은 지금처럼 가운데 창", () => {
    setMedia({ mobile: false });
    mockFetch({});
    renderWithProviders(<KillSwitch status="RUNNING" />);
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    expect(screen.getByRole("dialog", { name: /킬 스위치를 작동할까요/ })).toHaveClass("modal");
  });

  it("보유 종목 줄을 누르면 종목 빠른 보기(가격·등락·흐름·내 보유), 닫기로 닫힌다", async () => {
    setMedia({ mobile: true, reduce: true });
    const fetchMock = mockFetch({
      "/api/market/005930/candles": { name: "삼성전자", bars: BARS, markers: [] },
    });
    renderWithProviders(
      <>
        <HoldingsTable positions={[POS]} />
        <StockSheetHost />
        <Where />
      </>,
    );
    fireEvent.click(screen.getByRole("link", { name: "삼성전자" }));
    // 휴대폰에서는 화면을 옮기지 않는다
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/);
    const sheet = await screen.findByRole("dialog", { name: "삼성전자 빠른 보기" });
    expect(await within(sheet).findByTestId("mini-chart")).toBeInTheDocument();
    expect(sheet).toHaveTextContent("90,500");
    expect(sheet).toHaveTextContent("▲ 200 (+0.22%)");
    expect(sheet.querySelector('dl[aria-label="내 보유"]')).not.toBeNull();
    expect(sheet).toHaveTextContent("10주");
    expect(sheet).toHaveTextContent("+29.29%");
    // 종목 화면과 같은 일봉 캐시를 쓴다
    expect(fetchMock.mock.calls.map(([u]) => u)).toContain("/api/market/005930/candles?days=180");
    expect(within(sheet).getByRole("link", { name: "종목 상세 보기 →" })).toHaveAttribute(
      "href",
      "/stock/005930",
    );
    fireEvent.click(within(sheet).getByRole("button", { name: "닫기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("넓은 화면에서는 줄을 눌러도 시트 없이 종목 화면으로", () => {
    setMedia({ mobile: false });
    mockFetch({});
    renderWithProviders(
      <>
        <HoldingsTable positions={[POS]} />
        <StockSheetHost />
        <Where />
      </>,
    );
    fireEvent.click(screen.getByRole("link", { name: "삼성전자" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/stock/005930");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

/** 손가락 이벤트 (jsdom 의 TouchEvent 는 touches 를 받지 않는다 — 필요한 것만 붙인다) */
function touch(el: Element, type: string, y?: number) {
  const ev = new Event(type, { bubbles: true, cancelable: true });
  const list = y == null ? [] : [{ clientX: 100, clientY: y }];
  Object.defineProperty(ev, "touches", { value: list });
  el.dispatchEvent(ev);
  return ev;
}

let polled = 0;
function Polled() {
  const q = useQuery({ queryKey: ["KR", "probe"], queryFn: async () => ++polled });
  return <div>값 {q.data}</div>;
}

describe("당겨서 새로고침 (휴대폰)", () => {
  it("맨 위에서 당겼다 놓으면 지금 화면의 데이터를 다시 받는다", async () => {
    setMedia({ mobile: true, reduce: true });
    polled = 0;
    mockFetch({});
    renderWithProviders(layout(<Polled />), { markets: [KR_MARKET] });
    expect(await screen.findByText("값 1")).toBeInTheDocument();
    const scroller = screen.getByTestId("page-scroll");
    await waitFor(() => expect(scroller).toHaveAttribute("data-ptr", "ready")); // 손가락 처리 조각 도착
    touch(scroller, "touchstart", 100);
    act(() => {
      for (let y = 110; y <= 300; y += 30) touch(scroller, "touchmove", y);
    });
    expect(scroller).toHaveClass("ptr-on");
    expect(screen.getByTestId("ptr")).toHaveClass("armed");
    act(() => void touch(scroller, "touchend"));
    expect(screen.getByTestId("ptr")).toHaveClass("refreshing");
    expect(await screen.findByText("값 2")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("ptr")).toHaveClass("idle"), { timeout: 2000 });
    expect(scroller).not.toHaveClass("ptr-on");
  });

  it("조금만 당기면(문턱 전) 새로고침하지 않고, 넓은 화면에서는 손가락 처리를 붙이지 않는다", async () => {
    setMedia({ mobile: true, reduce: true });
    polled = 0;
    mockFetch({});
    const view = renderWithProviders(layout(<Polled />), { markets: [KR_MARKET] });
    expect(await screen.findByText("값 1")).toBeInTheDocument();
    const scroller = screen.getByTestId("page-scroll");
    await waitFor(() => expect(scroller).toHaveAttribute("data-ptr", "ready"));
    touch(scroller, "touchstart", 100);
    act(() => void touch(scroller, "touchmove", 150));
    act(() => void touch(scroller, "touchend"));
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.getByText("값 1")).toBeInTheDocument();
    view.unmount();

    setMedia({ mobile: false });
    renderWithProviders(layout(<Polled />), { markets: [KR_MARKET] });
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.getByTestId("page-scroll")).not.toHaveAttribute("data-ptr");
  });
});

describe("살아 있는 숫자", () => {
  const won = (v: number | null | undefined) =>
    v == null ? "—" : `${Math.round(v).toLocaleString("en-US")}원`;

  it("값이 바뀌면 이전 값에서 굴러가며 바뀌고 오르면 빨강으로 깜빡인다", async () => {
    setMedia({});
    const { rerender, container } = render(<AnimatedNumber value={1000} format={won} />);
    const el = container.querySelector(".num")!;
    expect(el).toHaveTextContent("1,000원");
    rerender(<AnimatedNumber value={2000} format={won} />);
    // 그리기 전에 시작 값으로 되돌려 두고 굴리기 시작한다
    expect(el).toHaveTextContent("1,000원");
    expect(el).toHaveClass("flash-up");
    await waitFor(() => expect(el).toHaveTextContent("2,000원"));
    rerender(<AnimatedNumber value={1500} format={won} />);
    expect(el).toHaveClass("flash-down");
    await waitFor(() => expect(el).toHaveTextContent("1,500원"));
  });

  it("'동작 줄이기'면 굴리지도 깜빡이지도 않고 바로 바꾼다", () => {
    setMedia({ reduce: true });
    const { rerender, container } = render(<AnimatedNumber value={1000} format={won} />);
    const el = container.querySelector(".num")!;
    rerender(<AnimatedNumber value={2000} format={won} />);
    expect(el).toHaveTextContent("2,000원");
    expect(el).not.toHaveClass("flash-up");
  });

  it("처음 받은 값·같은 값으로 다시 받은 값은 굴리지 않는다", () => {
    setMedia({});
    const { rerender, container } = render(<AnimatedNumber value={undefined} format={won} />);
    const el = container.querySelector(".num")!;
    expect(el).toHaveTextContent("—");
    rerender(<AnimatedNumber value={1000} format={won} />);
    expect(el).toHaveTextContent("1,000원");
    expect(el).not.toHaveClass("flash-up");
    rerender(<AnimatedNumber value={1000} format={won} />);
    expect(el).not.toHaveClass("flash-up");
  });
});

describe("상대 시각", () => {
  const now = Date.parse("2026-11-05T10:00:00+09:00");
  it("방금 · N분 전 · N시간 전 · 어제 HH:MM · N일 전 · 일주일 넘으면 날짜", () => {
    expect(fmtAgo("2026-11-05T09:59:40+09:00", now)).toBe("방금");
    expect(fmtAgo("2026-11-05T09:57:00+09:00", now)).toBe("3분 전");
    expect(fmtAgo("2026-11-05T07:30:00+09:00", now)).toBe("2시간 전");
    expect(fmtAgo("2026-11-04T21:30:00+09:00", now)).toBe("어제 21:30");
    expect(fmtAgo("2026-11-02T10:00:00+09:00", now)).toBe("3일 전");
    expect(fmtAgo("2026-10-20T09:00:00+09:00", now)).toMatch(/10\. 20\./);
  });

  it("마우스를 올리면 정확한 한국 시각", () => {
    render(<RelTime iso="2026-11-04T21:30:00+09:00" />);
    const t = screen.getByText(/전|어제|\d/);
    expect(t.tagName).toBe("TIME");
    expect(t).toHaveAttribute("dateTime", "2026-11-04T21:30:00+09:00");
    expect(t.getAttribute("title")).toMatch(/2026.*21:30:00 KST/);
  });
});
