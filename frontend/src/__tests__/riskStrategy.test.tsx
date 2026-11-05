import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RiskConfig, RiskView } from "../api/types";
import { RiskSettings } from "../pages/settings/RiskSection";
import { riskText } from "../utils/risk";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const LYNCH: RiskConfig = {
  max_position_weight: 0.15,
  max_order_amount: 2_000_000,
  daily_loss_limit: -0.05,
  stop_loss: -0.2,
  take_profit: 1,
  min_order_interval_minutes: 1440,
  max_positions: 12,
  min_cash_ratio: 0.05,
  min_hold_days: 30,
  allow_new_buys: true,
};

describe("리스크 한도는 전략마다 (D-64)", () => {
  it("따르는 전략을 보여 주고, 고쳐 둔 값이면 전략 기본값으로 되돌릴 수 있다", async () => {
    const posts: string[] = [];
    const view = (custom: boolean): RiskView => ({
      config: custom ? { ...LYNCH, stop_loss: -0.1 } : LYNCH,
      strategy: { id: "lynch", name: "피터 린치", emoji: "🧺" },
      preset: LYNCH,
      custom,
    });
    mockFetch({
      "/api/risk/preset": (u: string) => (posts.push(u), view(false)),
      "/api/risk": view(true),
    });
    renderWithProviders(<RiskSettings />);
    expect(await screen.findByTestId("risk-strategy")).toHaveTextContent("🧺 피터 린치 전략의 한도예요");
    expect(screen.getByText(/전략 기본값\(손절 -20% · 익절 \+100% · 최소 보유 30일/)).toBeInTheDocument();
    expect(screen.getByLabelText("매수 후 최소 보유 일수 (손절·익절 예외)")).toHaveValue(30);
    fireEvent.click(screen.getByRole("button", { name: "전략 기본값으로" }));
    await waitFor(() => expect(posts).toEqual(["/api/risk/preset"]));
    expect(await screen.findByText("전략 기본값 그대로예요.")).toBeInTheDocument();
  });

  it("한 줄 요약: 보유 기간 제한이 없으면 그렇게 쓴다", () => {
    expect(riskText({ ...LYNCH, min_hold_days: 0 })).toBe(
      "손절 -20% · 익절 +100% · 보유 기간 제한 없음 · 종목당 15% · 최대 12종목",
    );
  });
});
