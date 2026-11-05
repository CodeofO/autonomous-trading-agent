import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { pageFor, preloadPage, SettingsPage } from "../routes";
import { Settings } from "../pages/Settings";
import { isSettingsSection, SECTION_PAGES, sectionOf } from "../pages/settings/lazy";
import { BOTH_MARKETS, mockFetch, renderWithProviders, SYSTEM } from "./utils";

// 화면 2단계: 설정을 주제별 화면으로 (넓은 화면 목록 + 내용, 휴대폰 목록 → 주제 화면)

// jsdom 에는 matchMedia 가 없다 — 휴대폰 폭인지 여부만 흉내 낸다
function setMobile(mobile: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: mobile && query.includes("max-width"), media: query })),
  );
}

afterEach(() => vi.unstubAllGlobals());

function Where() {
  const { pathname } = useLocation();
  return <span data-testid="where">{pathname}</span>;
}

const RISK = {
  config: {
    max_position_weight: 0.2,
    max_order_amount: 1_000_000,
    daily_loss_limit: -0.03,
    stop_loss: -0.05,
    take_profit: 0.1,
    min_order_interval_minutes: 60,
    max_positions: 10,
    min_cash_ratio: 0.05,
    allow_new_buys: true,
  },
};

function renderSettings(route: string, opts: { markets?: typeof BOTH_MARKETS; market?: "KR" | "US" } = {}) {
  mockFetch({
    "/api/system": {
      ...SYSTEM,
      llm: { configured: true, model: "qwen-7b", profile: "free", tool_mode: "auto" },
    },
    "/api/us/system": { ...SYSTEM, market: "US" },
    "/api/risk": RISK,
    "/api/us/risk": RISK,
  });
  return renderWithProviders(
    <>
      <Routes>
        <Route path="settings/:section?" element={<Settings />} />
      </Routes>
      <Where />
    </>,
    { route, markets: opts.markets, market: opts.market },
  );
}

describe("설정 주제 주소", () => {
  it("/settings/<주제> 를 읽고, 모르는 주제는 null", () => {
    expect(sectionOf("/settings/risk")).toBe("risk");
    expect(sectionOf("/settings/llm?x=1")).toBe("llm");
    expect(sectionOf("/settings")).toBeNull();
    expect(sectionOf("/settings/nope")).toBeNull();
    expect(isSettingsSection("alerts")).toBe(true);
    // 주제 화면도 설정 화면 조각으로 그린다 (예전 /settings 링크 그대로)
    expect(pageFor("/settings/risk", "KR")).toBe(SettingsPage);
    expect(pageFor("/settings", "KR")).toBe(SettingsPage);
  });

  it("설정 주소를 미리 받으면 화면 조각과 그 주제 조각을 함께 받는다", () => {
    const risk = vi.spyOn(SECTION_PAGES.risk, "preload").mockResolvedValue(undefined);
    const page = vi.spyOn(SettingsPage, "preload").mockResolvedValue(undefined);
    preloadPage("/settings/risk", "KR");
    expect(page).toHaveBeenCalled();
    expect(risk).toHaveBeenCalled();
    risk.mockRestore();
    page.mockRestore();
  });
});

describe("설정 — 넓은 화면 (왼쪽 목록 + 오른쪽 내용)", () => {
  it("/settings 는 목록과 LLM 모델을 함께 보여 주고, 목록을 누르면 주소만 바뀌며 오른쪽 내용이 바뀐다", async () => {
    setMobile(false);
    renderSettings("/settings");
    const nav = screen.getByRole("navigation", { name: "설정 주제" });
    // 공통 · 시장별 두 묶음 — 메뉴에 있는 화면(뉴스 룸·메모 카드)을 다시 두지 않고, LLM 비용만 설정 안에
    expect(within(nav).getByRole("region", { name: "앱 공통" })).toBeInTheDocument();
    expect(within(nav).queryByRole("region", { name: "바로 가기" })).not.toBeInTheDocument();
    expect(
      within(within(nav).getByRole("region", { name: "앱 공통" })).getByRole("link", {
        name: /LLM 요금/,
      }),
    ).toHaveAttribute("href", "/costs");
    expect(within(nav).queryByRole("link", { name: /뉴스 룸/ })).not.toBeInTheDocument();
    const pane = screen.getByTestId("settings-pane");
    expect(within(pane).getByRole("heading", { level: 2, name: /LLM 모델/ })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /LLM 모델/ })).toHaveAttribute("aria-current", "page");
    // 지금 값 한 줄 (상단 바가 받은 시장 상태로)
    await waitFor(() =>
      expect(within(nav).getByRole("link", { name: /LLM 모델/ })).toHaveTextContent("무료 · qwen-7b"),
    );

    fireEvent.click(within(nav).getByRole("link", { name: /리스크·주문/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/settings/risk");
    expect(within(nav).getByRole("link", { name: /리스크·주문/ })).toHaveAttribute("aria-current", "page");
    expect(
      within(screen.getByTestId("settings-pane")).getByRole("heading", { level: 2, name: /리스크·주문/ }),
    ).toBeInTheDocument();
    // 리스크 한도 폼은 따로 받는 조각 — 도착하면 서버 값으로 채운다
    expect(await screen.findByLabelText("종목당 최대 비중")).toHaveValue(20);
    // 넓은 화면에는 뒤로 가기 버튼이 없다 (목록이 늘 옆에 있다)
    expect(screen.queryByRole("button", { name: "설정 목록으로" })).not.toBeInTheDocument();
  });

  it("모르는 주제 주소는 설정 목록으로", async () => {
    setMobile(false);
    renderSettings("/settings/nope");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/settings$/));
  });

  it("시장마다 다른 주제는 어느 시장에 적용되는지 보여 준다", async () => {
    setMobile(false);
    renderSettings("/settings/risk", { markets: BOTH_MARKETS, market: "US" });
    expect(screen.getByTestId("settings-scope")).toHaveTextContent("🇺🇸 미장에만 적용돼요");
    expect(screen.getByRole("region", { name: "🇺🇸 미장 매니저" })).toBeInTheDocument();
    // 미장 리스크 한도는 /api/us/risk 로 받아 달러로 묻는다
    expect(await screen.findByLabelText("1회 주문 최대 금액(달러)")).toBeInTheDocument();
  });

  it("공통 주제(알림)는 '모든 시장 공통'", () => {
    setMobile(false);
    renderSettings("/settings/alerts", { markets: BOTH_MARKETS, market: "KR" });
    expect(screen.getByTestId("settings-scope")).toHaveTextContent("모든 시장 공통");
  });
});

describe("설정 — 휴대폰 (목록 → 주제 화면 → 뒤로)", () => {
  it("/settings 는 목록만, 누르면 주제 화면으로 들어가고 '‹ 설정'으로 목록에 돌아온다", async () => {
    setMobile(true);
    renderSettings("/settings");
    expect(screen.getByTestId("settings-hub")).toBeInTheDocument();
    expect(screen.queryByTestId("settings-pane")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: /리스크·주문/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/settings/risk");
    // 주제 화면에서는 목록을 그리지 않는다 (밀려 들어온 화면 하나)
    expect(screen.queryByTestId("settings-hub")).not.toBeInTheDocument();
    const pane = screen.getByTestId("settings-pane");
    expect(pane).toHaveClass("push");
    expect(await screen.findByLabelText("종목당 최대 비중")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "설정 목록으로" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/settings$/));
    const hub = screen.getByTestId("settings-hub");
    expect(hub).toHaveClass("pop"); // 돌아올 때는 왼쪽에서 밀려 나온다
    expect(screen.queryByTestId("settings-pane")).not.toBeInTheDocument();
  });

  it("주소로 바로 연 주제 화면(예: 상단 바 LLM 칩)도 '‹ 설정'이 목록으로 데려간다", async () => {
    setMobile(true);
    renderSettings("/settings/llm");
    expect(screen.getByTestId("settings-pane")).not.toHaveClass("push");
    fireEvent.click(screen.getByRole("button", { name: "설정 목록으로" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/settings$/));
    expect(screen.getByTestId("settings-hub")).toBeInTheDocument();
  });
});
