import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import type {
  LlmCosts,
  LlmLedgerDayGroup,
  LlmLedgerGroup,
  LlmLedgerMonthGroup,
  LlmOfficial,
} from "../api/types";
import { Layout } from "../components/Layout";
import { isActive, NAV_GROUPS, SETTINGS } from "../components/nav";
import { Costs } from "../pages/Costs";
import { Settings } from "../pages/Settings";
import { daysOf, groupBy, matches, NO_FILTER, optionValues, sum, tokenStack } from "../utils/costs";
import { fmtKrwFromUsd, fmtTokens, fmtUsd } from "../utils/format";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => {
  vi.unstubAllGlobals();
});

const g = (over: Partial<LlmLedgerGroup>): LlmLedgerGroup => ({
  profile: "paid",
  provider: "openai",
  model: "gpt-6.1-sol",
  market: "KR",
  strategy: "",
  kind: "session",
  calls: 1,
  input: 1_000,
  cache_read: 0,
  cache_known: 1,
  output: 100,
  reasoning: 0,
  cost: 0.01,
  unpriced: 0,
  by_source: { priced: 1 },
  ...over,
});

const M = (month: string, over: Partial<LlmLedgerGroup>): LlmLedgerMonthGroup => ({ month, ...g(over) });
const Dd = (day: string, over: Partial<LlmLedgerGroup>): LlmLedgerDayGroup => ({ day, ...g(over) });

const SOL = {
  calls: 10,
  input: 100_000,
  cache_read: 60_000,
  cache_known: 10,
  output: 5_000,
  reasoning: 2_000,
  cost: 1.5,
  by_source: { priced: 10 },
};
const QWEN = {
  profile: "free" as const,
  provider: "local" as const,
  model: "qwen",
  calls: 20,
  input: 300_000,
  cache_read: 0,
  cache_known: 0,
  output: 9_000,
  cost: 0,
  by_source: { free: 20 },
};
const LAB = { ...QWEN, strategy: "lynch", calls: 5, input: 50_000, output: 1_000, by_source: { free: 5 } };
const OR_ROW = {
  provider: "openrouter" as const,
  model: "deepseek/deepseek-v9",
  market: "US",
  calls: 4,
  input: 20_000,
  output: 800,
  cost: 0.25,
  by_source: { reported: 4 },
};
const MYSTERY = {
  provider: "gateway" as const,
  model: "mystery",
  kind: "chat",
  calls: 2,
  cost: 0,
  unpriced: 2,
  by_source: { unpriced: 2 },
};

const DATA: LlmCosts = {
  checked: "2026-11-04",
  sources: { openai: "https://example.com/openai-pricing", openrouter: "https://example.com/or" },
  today: "2026-11-05",
  months: ["2026-09", "2026-10", "2026-11"],
  month: "2026-11",
  groups: [
    M("2026-10", { ...SOL, cost: 3 }),
    M("2026-11", SOL),
    M("2026-11", QWEN),
    M("2026-11", LAB),
    M("2026-11", OR_ROW),
    M("2026-11", MYSTERY),
  ],
  daily: [
    Dd("2026-11-04", { ...SOL, cost: 1, calls: 6 }),
    Dd("2026-11-05", { ...SOL, cost: 0.5, calls: 4 }),
    Dd("2026-11-05", QWEN),
    Dd("2026-11-05", LAB),
    Dd("2026-11-05", OR_ROW),
    Dd("2026-11-03", MYSTERY),
  ],
  usd_krw: 1400,
  current_model: "gpt-6.1-sol",
  current_provider: "openai",
  profile: "paid",
  strategies: { default: { name: "기본 매니저", emoji: "🦉" }, lynch: { name: "피터 린치", emoji: "🧺" } },
  official_ready: { openai: false },
  prices: [
    {
      id: "gpt-6.1-sol",
      provider: "openai",
      name: "GPT-6.1 Sol",
      tier: "주력",
      input: 2,
      cached: 0.1,
      output: 10,
      cache_write: 2.5,
      long_from: null,
      long: null,
      next: null,
      note: "",
    },
    {
      id: "gemini-4-argon",
      provider: "google",
      name: "Gemini 4 Argon",
      tier: "주력",
      input: 2,
      cached: 0.1,
      output: 10,
      cache_write: null,
      long_from: null,
      long: null,
      next: { since: "2026-12-01", input: 4, cached: 0.2, output: 20 },
      note: "2026-12-01 부터 2배로 인상 예정",
    },
  ],
};

const OFFICIAL: LlmOfficial = {
  month: "2026-11",
  window_tz: "UTC",
  providers: [
    {
      provider: "openai",
      label: "OpenAI",
      status: "no_key",
      note: "OpenAI 공식 사용량·요금은 Admin 키로만 조회돼요.",
      ledger: { calls: 10, input: 100_000, cache_read: 60_000, output: 5_000, cost: 1.5 },
    },
    {
      provider: "openrouter",
      label: "OpenRouter",
      status: "ok",
      note: "키 1개의 공식 사용 금액(UTC 기준)이에요.",
      windows: [
        {
          key: "daily",
          label: "오늘",
          official: { cost: 0.25 },
          ledger: { calls: 4, input: 0, cache_read: 0, output: 0, cost: 0.25 },
        },
        {
          key: "monthly",
          label: "이번 달",
          official: { cost: 0.4 },
          ledger: { calls: 4, input: 0, cache_read: 0, output: 0, cost: 0.25 },
        },
      ],
    },
    {
      provider: "google",
      label: "Google",
      status: "unsupported",
      note: "Gemini API 키로 조회하는 공식 사용량·요금 API 가 없어요.",
    },
  ],
};

function mockCosts(data: LlmCosts = DATA, asked: string[] = []) {
  return mockFetch({
    "/api/llm/costs/official": OFFICIAL,
    "/api/llm/costs": (u: string) => {
      asked.push(u);
      const month = new URL(u, "http://x").searchParams.get("month");
      return month ? { ...data, month, daily: [] } : data;
    },
  });
}

describe("숫자 표기", () => {
  it("달러는 작은 금액일수록 소수 자리를 늘리고, 원은 반올림한다", () => {
    expect(fmtUsd(0)).toBe("$0");
    expect(fmtUsd(1234.567)).toBe("$1,234.57");
    expect(fmtUsd(0.0042)).toBe("$0.0042");
    expect(fmtUsd(0.00001)).toBe("<$0.0001");
    expect(fmtKrwFromUsd(1.5, 1343.8)).toBe("2,016원");
    expect(fmtKrwFromUsd(0.0001, 1343.8)).toBe("1원 미만");
  });

  it("토큰은 만·억 단위로 줄여 쓴다", () => {
    expect(fmtTokens(9_876)).toBe("9,876");
    expect(fmtTokens(123_456)).toBe("12.3만");
  });
});

describe("장부 묶음 계산", () => {
  const nov = DATA.groups.filter((x) => x.month === "2026-11");

  it("거르지 않으면 모두 더하고, 단가 모름은 요금에 넣지 않고 따로 센다", () => {
    const t = sum(nov);
    expect(t.calls).toBe(10 + 20 + 5 + 4 + 2);
    expect(t.cost).toBeCloseTo(1.75);
    expect(t.unpriced).toBe(2);
    expect(t.by_source).toEqual({ priced: 10, free: 25, reported: 4, unpriced: 2 });
  });

  it("빈 값(주 계좌·공용)도 정확히 거른다 — null 이 '전체'", () => {
    expect(sum(nov, { ...NO_FILTER, strategy: "" }).calls).toBe(10 + 20 + 4 + 2);
    expect(sum(nov, { ...NO_FILTER, strategy: "lynch" }).calls).toBe(5);
    expect(sum(nov, { ...NO_FILTER, profile: "free" }).cost).toBe(0);
    expect(matches(nov[0], { ...NO_FILTER, provider: "google" })).toBe(false);
  });

  it("거르기 목록은 다른 조건에 맞는 값만", () => {
    expect(optionValues(nov, "provider", NO_FILTER)).toEqual(["gateway", "local", "openai", "openrouter"]);
    expect(optionValues(nov, "model", { ...NO_FILTER, profile: "free" })).toEqual(["qwen"]);
  });

  it("묶어 합치기·막대 조각·날짜 칸", () => {
    const byModel = groupBy(nov, (x) => x.model);
    expect(byModel.get("qwen")?.calls).toBe(25);
    expect(tokenStack({ input: 100, cache_read: 150, output: 5 })).toEqual({
      cached: 100,
      rest: 0,
      output: 5,
      total: 105,
    });
    expect(daysOf("2026-11", "2026-11-05")).toEqual([
      "2026-11-01",
      "2026-11-02",
      "2026-11-03",
      "2026-11-04",
      "2026-11-05",
    ]);
    expect(daysOf("2026-10", "2026-11-05")).toHaveLength(31);
  });
});

describe("LLM 요금 화면", () => {
  it("이번 달 실제 요금·오늘 요금·출처를 보여 주고, 예상·캐시 시나리오는 없다", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    const total = await screen.findByTestId("costs-total");
    expect(total).toHaveTextContent("2026년 11월");
    expect(total).toHaveTextContent(fmtKrwFromUsd(1.75, 1400));
    expect(total).toHaveTextContent("오늘");
    expect(total).toHaveTextContent(fmtKrwFromUsd(0.75, 1400)); // 0.5 + 0 + 0 + 0.25
    expect(screen.getByLabelText("요금 출처")).toHaveTextContent("제공사 보고 4회");
    expect(screen.getByLabelText("요금 출처")).toHaveTextContent("단가 모름 2회");
    expect(screen.queryByText(/캐시 적용/)).not.toBeInTheDocument();
    expect(screen.queryByText(/예상/)).not.toBeInTheDocument();
    expect(screen.getByText("gpt-6.1-sol", { selector: ".costs-model" })).toBeInTheDocument();
  });

  it("요금 카드 바로 밑에 주요 모델 단가표: 지금 모델 강조, 캐시 쓰기·예정된 인상, 자체 서버 0", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    const card = await screen.findByRole("region", { name: "주요 모델 단가" });
    expect(screen.getByTestId("costs-total").closest("section")!.nextElementSibling).toBe(card);
    const rows = within(card).getAllByRole("row");
    const sol = rows.find((r) => r.textContent?.includes("GPT-6.1 Sol"))!;
    expect(sol).toHaveAttribute("aria-current", "true");
    expect(sol).toHaveTextContent("지금");
    expect(sol).toHaveTextContent(fmtKrwFromUsd(2, 1400)); // 입력 100만 토큰당
    expect(sol).toHaveTextContent(`캐시 쓰기 ${fmtKrwFromUsd(2.5, 1400)}`);
    const argon = rows.find((r) => r.textContent?.includes("Gemini 4 Argon"))!;
    expect(argon).not.toHaveAttribute("aria-current");
    expect(argon).toHaveTextContent(`12월 1일부터 입력 ${fmtKrwFromUsd(4, 1400)}`);
    expect(card).toHaveTextContent("자체 서버 모델");
    fireEvent.click(screen.getByRole("button", { name: "$" }));
    expect(sol).toHaveTextContent("$2");
    expect(sol).toHaveTextContent("$0.1");
  });

  it("통화를 $ 로 바꾸면 달러로", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    fireEvent.click(await screen.findByRole("button", { name: "$" }));
    expect(screen.getByTestId("costs-total")).toHaveTextContent("$1.75");
  });

  it("무료/유료·계좌로 거르면 합계와 나눠 보기 표가 바뀐다", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    const profile = await screen.findByRole("combobox", { name: "무료/유료" });
    fireEvent.change(profile, { target: { value: "v:free" } });
    expect(screen.getByTestId("costs-total")).toHaveTextContent("호출 25회");
    const table = screen.getByTestId("costs-breakdown");
    expect(within(table).getByText("qwen")).toBeInTheDocument();
    expect(within(table).queryByText("gpt-6.1-sol")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "계좌" }));
    expect(
      within(screen.getByTestId("costs-breakdown")).getByText("🇰🇷 국장 · 🧺 피터 린치"),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "계좌" }), { target: { value: "v:" } });
    expect(screen.getByTestId("costs-total")).toHaveTextContent("호출 20회");

    fireEvent.click(screen.getByRole("button", { name: "모두 보기" }));
    expect(screen.getByTestId("costs-total")).toHaveTextContent("호출 41회");
  });

  it("나눠 보기: 모델별 표에 단가 모름·추론 토큰, 날짜별은 최근 날짜부터", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    const table = await screen.findByTestId("costs-breakdown");
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("gpt-6.1-sol"); // 요금 큰 순
    expect(within(table).getByText(/\+단가 모름 2회/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "날짜" }));
    const days = within(screen.getByTestId("costs-breakdown")).getAllByRole("row");
    expect(days[1]).toHaveTextContent("11월 5일 (목)");
  });

  it("월별 막대를 누르면 그 달의 일별 기록을 다시 받는다", async () => {
    const asked: string[] = [];
    mockCosts(DATA, asked);
    renderWithProviders(<Costs />);
    const chart = await screen.findByTestId("month-chart");
    fireEvent.click(within(chart).getByRole("button", { name: "2026년 10월" }));
    await waitFor(() => expect(asked.at(-1)).toContain("month=2026-10"));
    expect(await screen.findByRole("heading", { name: "일별 · 2026년 10월" })).toBeInTheDocument();
  });

  it("공식 기록과 맞춰 보기: 제공사마다 상태와 공식·장부 금액", async () => {
    mockCosts();
    renderWithProviders(<Costs />);
    const or = await screen.findByTestId("official-openrouter");
    expect(or).toHaveTextContent("맞춰 봄");
    expect(or).toHaveTextContent("이번 달");
    expect(screen.getByTestId("official-openai")).toHaveTextContent("키 없음");
    expect(screen.getByTestId("official-google")).toHaveTextContent("공식 조회 없음");
  });

  it("기록이 하나도 없으면 빈 화면 안내", async () => {
    mockCosts({ ...DATA, groups: [], daily: [] });
    renderWithProviders(<Costs />);
    expect(await screen.findByTestId("costs-empty")).toBeInTheDocument();
  });
});

describe("메뉴·설정 링크", () => {
  it("LLM 요금은 설정에 속한다: 사이드바에는 없고, 요금 화면에서는 오른쪽 위 ⚙️ 설정이 켜져 보인다", () => {
    mockFetch({});
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route path="costs" element={<div>요금 본문</div>} />
        </Route>
      </Routes>,
      { route: "/costs" },
    );
    expect(screen.queryByRole("link", { name: "LLM 요금" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");
    expect(isActive(SETTINGS, "/costs")).toBe(true);
  });

  it("설정 화면에 LLM 요금 링크가 있다", () => {
    mockFetch({});
    renderWithProviders(<Settings />);
    expect(screen.getByRole("link", { name: /^LLM 요금/ })).toHaveAttribute("href", "/costs");
  });

  it("사이드바 '매니저·분석'에 판단 일정·장외 거래 바로가기", () => {
    const group = NAV_GROUPS.find((x) => x.title === "매니저·분석")!;
    const item = group.items.find((n) => n.to === "/settings/schedule");
    expect(item?.label).toBe("판단 일정·장외 거래");
    expect(group.items.map((n) => n.to).indexOf("/settings/schedule")).toBe(
      group.items.map((n) => n.to).indexOf("/lab") + 1,
    );
  });
});
