import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Portfolio } from "../pages/Portfolio";
import { BOTH_MARKETS, mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const pos = (symbol: string, name: string, last: number) => ({
  symbol,
  name,
  quantity: 2,
  avg_price: last,
  last_price: last,
  market_value: last * 2,
  unrealized_pnl: 0,
  pnl_pct: 0,
  weight_pct: 10,
  holding_days: 1,
  price_day: "2026-11-05",
});
const order = (id: number, symbol: string, status: string, at: string) => ({
  id,
  symbol,
  name: symbol,
  side: "BUY",
  quantity: 1,
  status,
  created_at: at,
  trade_day: "2026-11-05",
  fill_basis: "CLOSE",
  filled_qty: 0,
  avg_fill_price: null,
  reason: "테스트",
  reject_reason: null,
});

describe("포트폴리오 '전체' 보기 (D-61)", () => {
  it("국장·미장 보유 종목을 시장별로, 주문·체결은 한 표에 시장 표시와 함께 시간순으로", async () => {
    const cancels: string[] = [];
    mockFetch({
      // 앞에서부터 주소 앞부분이 맞는 것을 쓴다 — 취소 주소를 목록 주소보다 먼저
      "/api/orders/3/cancel": (u: string) => (cancels.push(u), {}),
      "/api/us/orders/7/cancel": (u: string) => (cancels.push(u), {}),
      "/api/us/portfolio": { equity: 10_500, cash: 500, positions: [pos("NVDA", "엔비디아", 190)] },
      "/api/us/orders": [order(7, "NVDA", "SUBMITTED", "2026-11-05T00:10:00+09:00")],
      "/api/us/fills": [],
      "/api/us/agent/decisions": [],
      "/api/portfolio": {
        equity: 10_000_000,
        cash: 1_000_000,
        positions: [pos("005930", "삼성전자", 70_000)],
      },
      "/api/orders": [
        order(3, "005930", "PENDING", "2026-11-05T09:10:00+09:00"),
        order(2, "000660", "FILLED", "2026-11-04T10:00:00+09:00"),
      ],
      "/api/fills": [],
      "/api/agent/decisions": [],
      "/api/overview": {
        fx: { usdkrw: 1400, at: null },
        markets: [],
        total_krw: { equity: 24_700_000, pnl_today: 0 },
      },
    });
    renderWithProviders(<Portfolio />, { markets: BOTH_MARKETS, market: "ALL", route: "/portfolio" });
    expect(await screen.findByTestId("portfolio-all")).toBeInTheDocument();
    expect(await screen.findByText("24,700,000원")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /국장 보유 종목/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /미장 보유 종목/ })).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("엔비디아").length).toBeGreaterThan(0));

    const openCard = screen.getByRole("heading", { name: /미체결 주문 \(2\)/ }).closest("section")!;
    const rows = within(openCard).getAllByRole("row").slice(1);
    // 최근 것이 위: 국장 09:10 → 미장 00:10
    expect(rows.map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual(["🇰🇷", "🇺🇸"]);
    fireEvent.click(within(rows[1]).getByRole("button", { name: "취소" }));
    await waitFor(() => expect(cancels).toEqual(["/api/us/orders/7/cancel"]));
  });

  it("국장·미장을 고르면 그 시장만", async () => {
    mockFetch({
      "/api/portfolio": { equity: 1, cash: 1, positions: [] },
      "/api/orders": [],
      "/api/fills": [],
      "/api/agent/decisions": [],
    });
    renderWithProviders(<Portfolio />, { markets: BOTH_MARKETS, market: "KR", route: "/portfolio" });
    expect(await screen.findByRole("heading", { name: "보유 종목" })).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-all")).not.toBeInTheDocument();
  });
});
