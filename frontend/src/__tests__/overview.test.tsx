import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import type { ChatMsg, MarketInfo, Overview as OverviewData } from "../api/types";
import { EventsProvider } from "../events";
import { useMarket } from "../market";
import { Overview } from "../pages/Overview";
import { mergeFeed, timelinePos } from "../utils/overview";
import { BOTH_MARKETS, KR_MARKET, mockFetch, renderWithProviders, US_MARKET } from "./utils";

class FakeWS {
  static OPEN = 1;
  readyState = 0;
  onopen = null;
  onclose = null;
  onmessage = null;
  constructor(public url: string) {}
  send() {}
  close() {}
}

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

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-04T22:21:00+09:00"));
  vi.stubGlobal("WebSocket", FakeWS);
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const OVERVIEW: OverviewData = {
  fx: { usdkrw: 1342.45, at: "2026-11-04T22:21:32.000+09:00" },
  markets: [
    {
      code: "KR",
      name: "국장",
      currency: "KRW",
      equity: 9_984_359,
      cash: 3_729_759,
      invested: 6_254_600,
      pnl_today: 108_724,
      pnl_today_pct: 1.1,
      total_return_pct: -0.16,
      positions: 5,
      top_positions: [
        { symbol: "105560", name: "KB금융", weight: 17.0, pnl_pct: 1.38 },
        { symbol: "012330", name: "현대모비스", weight: 14.5, pnl_pct: 2.92 },
        { symbol: "003490", name: "대한항공", weight: 11.9, pnl_pct: -0.76 },
        { symbol: "010140", name: "삼성중공업", weight: 9.8, pnl_pct: 2.78 },
        { symbol: "047050", name: "포스코인터내셔널", weight: 9.4, pnl_pct: 2.89 },
      ],
      engine_status: "RUNNING",
      agent_running: false,
      next_run: { name: "morning", at: "2026-11-05T08:35:00+09:00" },
      last_session: {
        run_id: 73,
        at: "2026-11-04T12:45:31+00:00",
        status: "DONE",
        summary: "야간 점검 완료! 보유 5종목은 모두 유지하고 현금 약 37.4%를 지켜둘게요.",
      },
    },
    {
      code: "US",
      name: "미장",
      currency: "USD",
      equity: 10_000,
      cash: 10_000,
      invested: 0,
      pnl_today: 0,
      pnl_today_pct: 0,
      total_return_pct: 0,
      positions: 0,
      top_positions: [],
      engine_status: "RUNNING",
      agent_running: true,
      next_run: { name: "morning", at: "2026-11-04T23:05:00+09:00" },
      last_session: null,
    },
  ],
  total_krw: { equity: 23_408_859, pnl_today: 108_724 },
};

const msg = (over: Partial<ChatMsg> & Pick<ChatMsg, "id" | "ts" | "kind">): ChatMsg => ({
  role: "analyst",
  content: "",
  run_id: 1,
  payload: {},
  ...over,
});

const KR_CHAT: ChatMsg[] = [
  msg({ id: 1, ts: "2026-11-04T01:00:00Z", kind: "tool", content: "📰 뉴스 검색" }),
  msg({
    id: 2,
    ts: "2026-11-04T01:01:00Z",
    kind: "decision",
    content: "🔴 매수 KB금융",
    payload: { action: "BUY", symbol: "105560", name: "KB금융", quantity: 11, confidence: 0.7 },
  }),
  msg({
    id: 3,
    ts: "2026-11-04T01:01:01Z",
    kind: "decision",
    content: "⚪ 유지 NAVER",
    payload: { action: "HOLD", symbol: "035420", name: "NAVER", confidence: 0.5 },
  }),
  msg({
    id: 4,
    ts: "2026-11-04T01:02:00Z",
    role: "broker",
    kind: "fill",
    content: "💸 체결! KB금융(105560) 매수 11주 @ 152,599원",
  }),
  msg({ id: 5, ts: "2026-11-04T01:03:00Z", kind: "summary", content: "정시 점검 완료! 🦉" }),
];

const US_CHAT: ChatMsg[] = [
  msg({
    id: 1,
    ts: "2026-11-04T14:40:00Z",
    kind: "decision",
    content: "🔵 매도 애플",
    payload: { action: "SELL", symbol: "AAPL", name: "애플", quantity: 3, confidence: 0.6 },
  }),
  msg({ id: 2, ts: "2026-11-04T13:00:00Z", role: "system", kind: "summary", content: "📊 미장 마감 리포트" }),
];

const IDLE = { active: false, typing: null, replying: false };

function Here() {
  const { pathname } = useLocation();
  // 고른 값(chosen)은 '전체' 그대로, 지금 보이는 시장(view)만 바뀐다
  const { chosen, view } = useMarket();
  return <span data-testid="here">{`${pathname} ${chosen} ${view}`}</span>;
}

function renderOverview(overview: unknown = OVERVIEW, markets: MarketInfo[] = BOTH_MARKETS) {
  const fetchMock = mockFetch({
    "/api/overview": overview,
    "/api/us/chat": US_CHAT,
    "/api/us/agent/progress": IDLE,
    "/api/us/events": [],
    "/api/chat": KR_CHAT,
    "/api/agent/progress": IDLE,
    "/api/events": [],
  });
  const view = renderWithProviders(
    <EventsProvider>
      <Routes>
        <Route index element={<Overview />} />
        <Route path="room" element={<Here />} />
        <Route path="stock/:symbol" element={<Here />} />
      </Routes>
    </EventsProvider>,
    { markets, market: "ALL" },
  );
  return { fetchMock, ...view };
}

describe("한눈에 보기 (전체)", () => {
  it("원화 총자산·오늘 손익·환율과 시장별 비중 막대", async () => {
    renderOverview();
    expect(await screen.findByTestId("ov-total")).toHaveTextContent("23,408,859원");
    expect(screen.getByTestId("ov-total-pnl")).toHaveTextContent("오늘 +108,724원 (+0.47%)");
    expect(screen.getByTestId("ov-fx")).toHaveTextContent("환율 1달러 = 1,342.45원");
    // 국장 9,984,359원 : 미장 $10,000 × 1,342.45 = 13,424,500원
    expect(screen.getByTestId("ov-split-KR")).toHaveTextContent("국장 42.7%");
    expect(screen.getByTestId("ov-split-US")).toHaveTextContent("미장 57.3%");
    expect(screen.getByTestId("ov-split-US")).toHaveTextContent("13,424,500원");
  });

  it("시장 카드: 통화별 총자산(미장은 원화 환산), 보유 상위, 매니저 상태, 마지막 세션", async () => {
    renderOverview();
    const kr = screen.getByTestId("ov-market-KR");
    expect(await within(kr).findByTestId("ov-equity-KR")).toHaveTextContent("9,984,359원");
    expect(kr).toHaveTextContent("오늘 +108,724원 (+1.10%)");
    expect(kr).toHaveTextContent("보유 5종목");
    const rows = within(within(kr).getByRole("list", { name: "국장 보유 상위" })).getAllByRole("listitem");
    expect(rows).toHaveLength(5);
    expect(rows[0]).toHaveTextContent("KB금융17.0%+1.38%");
    expect(within(kr).getByText("장중")).toBeInTheDocument(); // KR_MARKET.open_now
    expect(within(kr).getByTestId("ov-last-KR")).toHaveTextContent("야간 점검 완료!");
    expect(within(kr).getByText("대기")).toBeInTheDocument();

    const us = screen.getByTestId("ov-market-US");
    expect(within(us).getByTestId("ov-equity-US")).toHaveTextContent("$10,000.00");
    expect(us).toHaveTextContent("≈ 13,424,500원");
    expect(within(us).getByText("모의")).toBeInTheDocument();
    expect(us).toHaveTextContent("보유 종목이 없어요");
    expect(within(us).getByText("판단 중…")).toBeInTheDocument();
    expect(us).toHaveTextContent("아직 판단 세션이 없어요");
  });

  it("오늘 장 시간: 국장 낮·미장 밤(자정 넘김)과 지금 표시", async () => {
    renderOverview();
    const kr = screen.getByTestId("ov-tl-KR");
    const us = screen.getByTestId("ov-tl-US");
    const krBar = kr.querySelector(".ov-tl-bar") as HTMLElement;
    const usBar = us.querySelector(".ov-tl-bar") as HTMLElement;
    expect(krBar).toHaveTextContent("09:00–15:30");
    expect(usBar).toHaveTextContent("23:30–06:00");
    // 06:00 → 다음날 06:00 시간표: 09:00 = 12.5%, 23:30 = 72.9%, 미장은 끝(100%)까지
    expect(parseFloat(krBar.style.left)).toBeCloseTo(12.5, 1);
    expect(parseFloat(usBar.style.left)).toBeCloseTo(72.92, 1);
    expect(parseFloat(usBar.style.left) + parseFloat(usBar.style.width)).toBeCloseTo(100, 1);
    expect(usBar).toHaveClass("next"); // 22:21 지금은 개장 전
    expect(screen.getByText("1시간 9분 후 개장")).toBeInTheDocument();
    expect(timelinePos(Date.parse("2026-11-04T22:21:00+09:00"))).toBeCloseTo(68.13, 1);
    expect(screen.getByTestId("ov-now")).toBeInTheDocument();
  });

  it("한국 아침: 미장 '현지 오늘'(지난밤 장)은 끝났으니 오늘 밤 장을 개장 전으로 그린다", async () => {
    vi.setSystemTime(new Date("2026-11-10T08:31:00+09:00"));
    const sess = (day: string, open: string, close: string) => ({ day, open, close, order_cutoff: close });
    renderOverview(OVERVIEW, [
      {
        ...KR_MARKET,
        open_now: false,
        session: sess("2026-11-10", "2026-11-10T09:00:00+09:00", "2026-11-10T15:30:00+09:00"),
        next_session: sess("2026-11-10", "2026-11-10T09:00:00+09:00", "2026-11-10T15:30:00+09:00"),
      },
      {
        ...US_MARKET,
        session: sess("2026-11-09", "2026-11-09T23:30:00+09:00", "2026-11-10T06:00:00+09:00"),
        next_session: sess("2026-11-10", "2026-11-10T23:30:00+09:00", "2026-11-11T06:00:00+09:00"),
        next_session_open: "2026-11-10T23:30:00+09:00",
      },
    ]);
    const kr = screen.getByTestId("ov-tl-KR");
    const us = screen.getByTestId("ov-tl-US");
    expect(screen.getByText("29분 후 개장")).toBeInTheDocument();
    expect(kr.querySelector(".ov-tl-bar")).toHaveClass("next");
    const usBar = us.querySelector(".ov-tl-bar") as HTMLElement;
    expect(usBar).toHaveClass("next");
    expect(usBar).toHaveTextContent("23:30–06:00");
    expect(screen.getByText("14시간 59분 후 개장")).toBeInTheDocument();
    expect(screen.queryByText("마감")).not.toBeInTheDocument();
  });

  it("주말 아침: 다음 미장이 시간표 밖이면 막대 없이 휴장과 다음 개장", async () => {
    vi.setSystemTime(new Date("2026-11-14T08:00:00+09:00"));
    const sess = (day: string, open: string, close: string) => ({ day, open, close, order_cutoff: close });
    renderOverview(OVERVIEW, [
      {
        ...US_MARKET,
        session: sess("2026-11-13", "2026-11-13T23:30:00+09:00", "2026-11-14T06:00:00+09:00"),
        next_session: sess("2026-11-16", "2026-11-16T23:30:00+09:00", "2026-11-17T06:00:00+09:00"),
        next_session_open: "2026-11-16T23:30:00+09:00",
      },
    ]);
    const us = screen.getByTestId("ov-tl-US");
    expect(us.querySelector(".ov-tl-bar")).toBeNull();
    expect(screen.getByText(/^휴장 · .*23:30 개장$/)).toBeInTheDocument();
  });

  it("두 시장 소식: 결정(유지 제외)·체결·세션 정리를 최신 순으로, 누르면 그 시장 트레이딩 룸", async () => {
    renderOverview();
    await waitFor(() => expect(screen.getAllByTestId("ov-feed-item")).toHaveLength(5));
    const items = screen.getAllByTestId("ov-feed-item");
    expect(items[0]).toHaveTextContent("미장");
    expect(items[0]).toHaveTextContent("매도 애플 3주");
    expect(items[1]).toHaveTextContent("마감 리포트");
    expect(items.map((i) => i.textContent).join(" ")).not.toContain("NAVER");
    expect(items.map((i) => i.textContent).join(" ")).not.toContain("뉴스 검색");
    expect(items[4]).toHaveTextContent("국장");
    expect(items[4]).toHaveTextContent("매수 KB금융 11주");

    fireEvent.click(items[0]);
    // '전체'는 그대로, 트레이딩 룸은 미장으로
    expect(screen.getByTestId("here")).toHaveTextContent("/room ALL US");
    expect(window.localStorage.getItem("market.last")).toBe("US");
  });

  it("보유 종목을 누르면 그 시장 종목 화면", async () => {
    renderOverview();
    fireEvent.click(await screen.findByRole("link", { name: "KB금융" }));
    expect(screen.getByTestId("here")).toHaveTextContent("/stock/105560 ALL KR");
  });

  it("처음 불러오는 동안은 자리 표시", async () => {
    const { fetchMock } = renderOverview(() => new Promise(() => {}));
    const total = screen.getByRole("region", { name: "전체 총자산" });
    expect(total).toHaveAttribute("aria-busy", "true");
    expect(total.querySelector(".ov-skel")).not.toBeNull();
    expect(screen.getByTestId("ov-market-KR").querySelector(".ov-skel")).not.toBeNull();
    expect(fetchMock.mock.calls.filter(([u]) => u === "/api/overview")).toHaveLength(1);
  });

  it("소식 섞기: 시장 표시를 붙이고 15개까지", () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      msg({ id: i, ts: new Date(Date.UTC(2026, 10, 4, 0, i)).toISOString(), kind: "fill", role: "broker" }),
    );
    const out = mergeFeed([
      { code: "KR", messages: many },
      { code: "US", messages: US_CHAT },
    ]);
    expect(out).toHaveLength(15);
    expect(out[0].code).toBe("US");
    expect(out.every((x) => x.msg.kind !== "decision" || x.msg.payload.action !== "HOLD")).toBe(true);
  });
});
