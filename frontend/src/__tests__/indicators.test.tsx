import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Indicator, MarketIndicators } from "../api/types";
import { IndicatorStrip } from "../components/IndicatorStrip";
import { EventsProvider } from "../events";
import { Dashboard } from "../pages/Dashboard";
import { fmtIndicator, fmtIndicatorChange } from "../utils/format";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

const spark = (from: number, to: number) =>
  Array.from({ length: 30 }, (_, i) => from + ((to - from) * i) / 29);

const base = { note: null, prev_close: null, change: null, change_pct: null, direction: null, error: false };

const ITEMS: Indicator[] = [
  {
    ...base,
    key: "KOSPI",
    name: "코스피",
    kind: "index",
    unit: "pt",
    price: 5648.79,
    prev_close: 5859.08,
    change: -210.29,
    change_pct: -3.59,
    spark: spark(5900, 5648.79),
  },
  {
    ...base,
    key: "KOSDAQ",
    name: "코스닥",
    kind: "index",
    unit: "pt",
    price: null,
    error: true,
    spark: [],
  },
  {
    ...base,
    key: "QQQ",
    name: "나스닥100",
    kind: "etf",
    unit: "$",
    note: "QQQ ETF",
    price: 686.13,
    prev_close: 673.79,
    change: 12.34,
    change_pct: 1.83,
    spark: spark(650, 686.13),
  },
  {
    ...base,
    key: "SPY",
    name: "S&P500",
    kind: "etf",
    unit: "$",
    note: "SPY ETF",
    price: 712.4,
    prev_close: 712.4,
    change: 0,
    change_pct: 0,
    spark: [712.4],
  },
  {
    ...base,
    key: "USDKRW",
    name: "원/달러",
    kind: "fx",
    unit: "원",
    price: 1344.5,
    prev_close: 1340,
    direction: "up",
    spark: [],
  },
  {
    ...base,
    key: "KR_BOND_10Y",
    name: "국고채 10년",
    kind: "bond",
    unit: "%",
    price: 4.002,
    prev_close: 4.014,
    change: -0.012,
    spark: spark(4.1, 4.002),
  },
];

const PAYLOAD: MarketIndicators = { as_of: "2026-11-04T08:45:00+09:00", items: ITEMS };

const card = (name: string) => screen.getByText(name).closest(".ind-card") as HTMLElement;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("주요 지표 숫자 표기", () => {
  it("단위별로 값을 쓴다", () => {
    expect(fmtIndicator(5648.79, "pt")).toBe("5,648.79");
    expect(fmtIndicator(12, "pt")).toBe("12.00");
    expect(fmtIndicator(686.13, "$")).toBe("$686.13");
    expect(fmtIndicator(1234.5, "$")).toBe("$1,234.50");
    expect(fmtIndicator(1344.5, "원")).toBe("1,344.5원");
    expect(fmtIndicator(1344.25, "원")).toBe("1,344.25원");
    expect(fmtIndicator(4.002, "%")).toBe("4.002%");
    expect(fmtIndicator(null, "pt")).toBe("—");
  });

  it("전일 대비: 지수·ETF 는 변화량과 등락률, 채권은 %p, 환율은 화살표만", () => {
    const m = { kind: "index", change: 12.34, change_pct: 0.45, direction: null } as const;
    expect(fmtIndicatorChange(m)).toBe("▲ 12.34 (+0.45%)");
    expect(fmtIndicatorChange({ ...m, change: -1210.29, change_pct: -3.59 })).toBe("▼ 1,210.29 (-3.59%)");
    expect(fmtIndicatorChange({ ...m, kind: "etf", change: null, change_pct: 0.45 })).toBe("▲ +0.45%");
    expect(fmtIndicatorChange({ ...m, change: null, change_pct: null })).toBe("");
    expect(fmtIndicatorChange({ ...m, kind: "bond", change: 0.012, change_pct: null })).toBe("▲ 0.012%p");
    expect(fmtIndicatorChange({ ...m, kind: "bond", change: -0.012, change_pct: null })).toBe("▼ 0.012%p");
    const fx = { kind: "fx", change: null, change_pct: null } as const;
    expect(fmtIndicatorChange({ ...fx, direction: "up" })).toBe("▲");
    expect(fmtIndicatorChange({ ...fx, direction: "down" })).toBe("▼");
    expect(fmtIndicatorChange({ ...fx, direction: null })).toBe("");
  });
});

describe("주요 지표 줄", () => {
  it("지표마다 카드 하나: 값·등락·색(상승 빨강/하락 파랑)·ETF 대용 표시", async () => {
    mockFetch({ "/api/market/indicators": PAYLOAD });
    renderWithProviders(<IndicatorStrip />);
    await screen.findByText("5,648.79");

    const strip = screen.getByRole("region", { name: "주요 지표" });
    expect(strip.querySelectorAll(".ind-card")).toHaveLength(6);

    const kospi = card("코스피");
    expect(kospi).toHaveClass("down");
    expect(within(kospi).getByText("▼ 210.29 (-3.59%)")).toHaveClass("ind-chg", "down");
    expect(kospi.querySelector("svg.ind-spark")).toHaveClass("down");
    expect(kospi).toHaveAttribute("title", expect.stringContaining("전일 종가 5,859.08"));

    const qqq = card("나스닥100");
    expect(qqq).toHaveClass("up");
    expect(within(qqq).getByText("QQQ ETF")).toHaveClass("ind-note");
    expect(within(qqq).getByText("$686.13")).toBeInTheDocument();
    expect(within(qqq).getByText("▲ 12.34 (+1.83%)")).toHaveClass("up");
    expect(qqq.querySelector("svg.ind-spark")).toHaveClass("up");
    expect(within(card("S&P500")).getByText("SPY ETF")).toBeInTheDocument();

    const fx = card("원/달러");
    expect(within(fx).getByText("1,344.5원")).toBeInTheDocument();
    expect(within(fx).getByLabelText("전일 대비 상승")).toHaveTextContent(/^▲$/);
    expect(fx).toHaveClass("up");

    const bond = card("국고채 10년");
    expect(within(bond).getByText("4.002%")).toBeInTheDocument();
    expect(within(bond).getByText("▼ 0.012%p")).toHaveClass("down");
  });

  it("보합은 색이 없고, 점이 2개 미만이면 선 그래프를 그리지 않는다", async () => {
    mockFetch({ "/api/market/indicators": PAYLOAD });
    renderWithProviders(<IndicatorStrip />);
    await screen.findByText("5,648.79");
    const spy = card("S&P500");
    expect(spy).not.toHaveClass("up");
    expect(spy).not.toHaveClass("down");
    expect(within(spy).getByText("0.00 (0.00%)")).toBeInTheDocument();
    expect(spy.querySelector("svg")).toBeNull();
    expect(card("원/달러").querySelector("svg")).toBeNull();
    expect(screen.getAllByTestId("sparkline")).toHaveLength(3);
  });

  it("값을 못 받은 지표는 '—' 로 두고 나머지는 그대로 보여 준다", async () => {
    mockFetch({ "/api/market/indicators": PAYLOAD });
    renderWithProviders(<IndicatorStrip />);
    await screen.findByText("5,648.79");
    const kosdaq = card("코스닥");
    expect(kosdaq).toHaveClass("failed");
    expect(kosdaq.querySelector(".ind-price")).toHaveTextContent("—");
    expect(kosdaq.querySelector("svg")).toBeNull();
    expect(kosdaq).toHaveAttribute("title", expect.stringContaining("불러오지 못했어요"));
  });

  it("받는 동안은 같은 높이의 자리 표시 6칸, 처음부터 실패하면 줄을 숨긴다", async () => {
    let resolve: (v: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((r) => (resolve = r))),
    );
    const { container, unmount } = renderWithProviders(<IndicatorStrip />);
    const strip = screen.getByRole("region", { name: "주요 지표" });
    expect(strip).toHaveAttribute("aria-busy", "true");
    expect(strip.querySelectorAll(".ind-card.skel")).toHaveLength(6);
    resolve(new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    unmount();

    mockFetch({});
    const second = renderWithProviders(<IndicatorStrip />);
    await waitFor(() => expect(second.container.querySelector(".ind-strip")).toBeNull());
    expect(container).toBeEmptyDOMElement();
  });

  it("대시보드 맨 위(핵심 숫자 카드 위)에 나온다", async () => {
    // 실시간 이벤트 웹소켓은 열지 않는다 (네트워크 없음)
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
    mockFetch({
      "/api/market/indicators": PAYLOAD,
      "/api/system": SYSTEM,
      "/api/agent/progress": { active: false, typing: null, replying: false },
      "/api/chat": [],
    });
    renderWithProviders(
      <EventsProvider>
        <Dashboard />
      </EventsProvider>,
    );
    await screen.findByText("5,648.79");
    const strip = screen.getByRole("region", { name: "주요 지표" });
    const kpis = screen.getByText("총자산").closest(".kpis")!;
    expect(strip.compareDocumentPosition(kpis) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
