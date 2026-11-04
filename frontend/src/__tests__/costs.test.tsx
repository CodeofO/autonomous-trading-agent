import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import type { LlmCosts, LlmModelCost, LlmProvider, LlmUsageMonth } from "../api/types";
import { Layout } from "../components/Layout";
import { isActive, SETTINGS } from "../components/nav";
import { Costs } from "../pages/Costs";
import { Settings } from "../pages/Settings";
import { extremes, sortModels, tokenStack } from "../utils/costs";
import { fmtKrwFromUsd, fmtTokens, fmtUsd } from "../utils/format";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => {
  vi.unstubAllGlobals();
});

const MONTHS = [
  "2025-12",
  "2026-01",
  "2026-02",
  "2026-03",
  "2026-04",
  "2026-05",
  "2026-06",
  "2026-07",
  "2026-08",
  "2026-09",
  "2026-10",
  "2026-11",
];

const zero = (month: string): LlmUsageMonth => ({
  month,
  calls: 0,
  input: 0,
  cached: 0,
  output: 0,
  sessions: 0,
  chats: 0,
  backfill: 0,
});

const USAGE: Record<string, Partial<LlmUsageMonth>> = {
  // 예전 세션 합계에서 가져온 달 (호출별 캐시 추정 없음)
  "2026-09": { calls: 12, input: 80_000, cached: 0, output: 3_000, sessions: 4, backfill: 4 },
  "2026-10": { calls: 30, input: 100_000, cached: 60_000, output: 5_000, sessions: 3, chats: 2 },
  "2026-11": { calls: 10, input: 50_000, cached: 20_000, output: 4_000, sessions: 1, chats: 1 },
};

const PROVIDERS: LlmProvider[] = ["anthropic", "openai", "google"];
const TIERS = ["최상위", "주력", "중간", "경량"];

/** 12개 모델: 회사마다 4개. 예상 요금은 일부러 순서와 다르게 섞는다 */
const PROJ = [1.5, 0.9, 0.3, 0.05, 2.4, 1.1, 0.2, 0.08, 1.8, 0.7, 0.12, 0.01];

function model(i: number): LlmModelCost {
  const provider = PROVIDERS[Math.floor(i / 4)];
  const cache = PROJ[i];
  return {
    id: `${provider}-${i}`,
    provider,
    name: `${provider === "anthropic" ? "Claude" : provider === "openai" ? "GPT" : "Gemini"} 모델${i}`,
    tier: TIERS[i % 4],
    note: i === 4 ? "입력 27.2만 토큰 초과 호출은 장문 요금" : "",
    price: {
      input: 4 - (i % 4),
      cached: 0.2,
      output: 20 - (i % 4) * 4,
      cache_write: provider === "anthropic" ? 5 : null,
      long_from: null,
    },
    months: Object.fromEntries(
      MONTHS.map((m) => [
        m,
        m === "2026-11"
          ? { cache: cache / 3, nocache: (cache * 2) / 3 }
          : m === "2026-10"
            ? { cache: cache * 0.8, nocache: cache * 1.6 }
            : { cache: 0, nocache: 0 },
      ]),
    ),
    projection: { cache, nocache: cache * 2 },
    daily_avg: { cache: cache / 20, nocache: cache / 10 },
  };
}

function costs(over: Partial<LlmCosts> = {}): LlmCosts {
  return {
    checked: "2026-11-04",
    sources: {
      anthropic: "https://example.com/anthropic-pricing",
      openai: "https://example.com/openai-pricing",
      google: "https://example.com/google-pricing",
    },
    current_model: "Qwen/Qwen3-32B",
    profile: "free",
    usd_krw: 1343.8,
    months: MONTHS.map((m) => ({ ...zero(m), ...USAGE[m] })),
    models: Array.from({ length: 12 }, (_, i) => model(i)),
    projection: { month: "2026-11", remaining_trading_days: 17, basis_days: 5, basis_window_days: 30 },
    ...over,
  };
}

function renderCosts(data: LlmCosts = costs()) {
  const fetch = mockFetch({ "/api/llm/costs": data });
  renderWithProviders(<Costs />);
  return fetch;
}

describe("LLM 비용 숫자 표기", () => {
  it("달러는 작은 금액일수록 소수 자리를 늘리고, 원은 반올림한다", () => {
    expect(fmtUsd(0)).toBe("$0");
    expect(fmtUsd(1234.567)).toBe("$1,234.57");
    expect(fmtUsd(0.123)).toBe("$0.123");
    expect(fmtUsd(0.0042)).toBe("$0.0042");
    expect(fmtUsd(0.00001)).toBe("<$0.0001");
    expect(fmtKrwFromUsd(1.5, 1343.8)).toBe("2,016원");
    expect(fmtKrwFromUsd(0.0001, 1343.8)).toBe("1원 미만");
  });

  it("토큰은 만·억 단위로 줄여 쓴다", () => {
    expect(fmtTokens(9_876)).toBe("9,876");
    expect(fmtTokens(123_456)).toBe("12.3만");
    expect(fmtTokens(345_000_000)).toBe("3.45억");
  });
});

describe("월별 토큰 막대", () => {
  it("입력(나머지) = 입력 − 캐시, 캐시가 입력보다 커도 입력을 넘지 않는다", () => {
    expect(tokenStack({ input: 100_000, cached: 60_000, output: 5_000 })).toEqual({
      cached: 60_000,
      rest: 40_000,
      output: 5_000,
      total: 105_000,
    });
    expect(tokenStack({ input: 10, cached: 50, output: 0 })).toMatchObject({ cached: 10, rest: 0 });
  });

  it("막대 조각을 캐시 적중 · 나머지 · 출력으로 쌓고, 달을 누르면 정확한 숫자와 세션·답장 수가 보인다", async () => {
    renderCosts();
    const chart = await screen.findByTestId("token-chart");
    const oct = chart.querySelector('g[data-month="2026-10"]')!;
    expect(oct.querySelector('[data-seg="llm-cached"]')).toHaveAttribute("data-value", "60000");
    expect(oct.querySelector('[data-seg="llm-input"]')).toHaveAttribute("data-value", "40000");
    expect(oct.querySelector('[data-seg="llm-output"]')).toHaveAttribute("data-value", "5000");
    expect(oct.querySelector("title")!.textContent).toContain("세션 3 · 답장 2");
    // 아무것도 안 쓴 달은 막대 조각이 없다
    expect(chart.querySelector('g[data-month="2026-01"] [data-seg]')).toBeNull();

    // 기본 선택은 이번 달
    expect(screen.getByTestId("token-detail")).toHaveTextContent("2026년 11월");
    fireEvent.click(within(chart).getByRole("button", { name: "10월" }));
    const detail = screen.getByTestId("token-detail");
    expect(detail).toHaveTextContent("캐시 적중 추정 60,000");
    expect(detail).toHaveTextContent("입력 나머지 40,000");
    expect(detail).toHaveTextContent("출력 5,000");
    expect(detail).toHaveTextContent("세션 3 · 답장 2");
  });

  it("예전 세션 합계를 가져온 달은 따로 표시한다", async () => {
    renderCosts();
    const chart = await screen.findByTestId("token-chart");
    const sep = within(chart).getByRole("button", { name: "9월" });
    expect(sep).toHaveClass("mark");
    expect(sep.title).toContain("예전 세션 합계 포함 — 호출별 캐시 추정 없음");
    expect(within(chart).getByRole("button", { name: "10월" })).not.toHaveClass("mark");
  });

  it("기록이 하나도 없으면 빈 화면 안내를 보여 준다", async () => {
    renderCosts(costs({ months: MONTHS.map(zero) }));
    expect(await screen.findByTestId("costs-empty")).toHaveTextContent("아직 기록된 LLM 사용량이 없어요");
    expect(screen.getByTestId("costs-empty")).toHaveTextContent("예전 판단 세션은 세션 합계로 가져왔어요");
    expect(screen.queryByTestId("token-chart")).toBeNull();
    expect(screen.queryByTestId("projection")).toBeNull();
    // 가격 비교표는 그대로 (가장 저렴/비쌈 표시는 없이)
    const table = screen.getByTestId("costs-table");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(12);
  });
});

describe("시나리오·통화 토글", () => {
  it("기본은 캐시 적용 · 원, 토글하면 예상 금액이 바뀐다", async () => {
    renderCosts();
    const proj = await screen.findByTestId("projection");
    // 첫 모델: 캐시 적용 $1.5 × 1,343.8 = 2,015.7 → 2,016원
    expect(proj).toHaveTextContent("2,016원");
    expect(screen.getByRole("button", { name: "캐시 적용(추정)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/남은 거래일 17일 \(최근 30일 중 사용한 5일 기준\)/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "캐시 없음" }));
    expect(screen.getByTestId("projection")).toHaveTextContent("4,031원"); // $3 × 1,343.8

    fireEvent.click(screen.getByRole("button", { name: "$" }));
    expect(screen.getByTestId("projection")).toHaveTextContent("$3.00");

    fireEvent.click(screen.getByRole("button", { name: "캐시 적용(추정)" }));
    expect(screen.getByTestId("projection")).toHaveTextContent("$1.50");
  });

  it("환율이 없으면 통화 토글 없이 $ 로만 보여 준다", async () => {
    renderCosts(costs({ usd_krw: null }));
    expect(await screen.findByTestId("projection")).toHaveTextContent("$1.50");
    expect(screen.queryByRole("group", { name: "통화" })).toBeNull();
    expect(screen.getByText("환율을 받지 못해 $ 로만 보여요")).toBeInTheDocument();
  });

  it("회사·모델을 고르면 그 모델의 예상과 월별 요금 막대로 바뀐다", async () => {
    renderCosts();
    await screen.findByTestId("projection");
    fireEvent.click(screen.getByRole("button", { name: "Google" }));
    // Google 첫 모델(8번): $1.8 → 2,419원
    expect(screen.getByTestId("projection")).toHaveTextContent("2,419원");
    const chips = screen.getByRole("group", { name: "모델" });
    expect(within(chips).getAllByRole("button")).toHaveLength(4);
    fireEvent.click(within(chips).getByRole("button", { name: /Gemini 모델11/ }));
    expect(screen.getByTestId("projection")).toHaveTextContent("13원"); // $0.01 × 1,343.8
    const nov = screen.getByTestId("cost-chart").querySelector('g[data-month="2026-11"]')!;
    expect(nov.querySelector('[data-seg="cost-spent"]')).not.toBeNull();
  });

  it("자체 서버 모델이면 지금은 API 요금이 없다고 알려 준다", async () => {
    renderCosts();
    expect(await screen.findByText("Qwen/Qwen3-32B")).toBeInTheDocument();
    expect(screen.getByText("지금은 API 요금 없음")).toBeInTheDocument();
    expect(screen.getByText("가격 확인일 2026-11-04")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Anthropic" })).toHaveAttribute(
      "href",
      "https://example.com/anthropic-pricing",
    );
  });
});

describe("12개 모델 비교표", () => {
  const order = () =>
    Array.from(screen.getByTestId("costs-table").querySelectorAll("tbody tr")).map((r) =>
      r.getAttribute("data-model"),
    );

  it("이번 달 예상이 싼 순서로 정렬하고, 머리글을 누르면 뒤집는다", async () => {
    renderCosts();
    await screen.findByTestId("costs-table");
    const asc = [...PROJ.keys()].sort((a, b) => PROJ[a] - PROJ[b]).map((i) => model(i).id);
    expect(order()).toEqual(asc);
    fireEvent.click(screen.getByRole("button", { name: /이번 달 예상/ }));
    expect(order()).toEqual([...asc].reverse());
    // 다른 열(출력 가격)로 정렬
    fireEvent.click(screen.getByRole("button", { name: /^출력/ }));
    const prices = order().map((id) => model(Number(id!.split("-")[1])).price.output);
    expect(prices).toEqual([...prices].sort((a, b) => a - b));
  });

  it("가장 저렴한 모델과 가장 비싼 모델을 표시한다", async () => {
    renderCosts();
    const table = await screen.findByTestId("costs-table");
    expect(table.querySelector("tr.cheapest")).toHaveAttribute("data-model", "google-11");
    expect(table.querySelector("tr.priciest")).toHaveAttribute("data-model", "openai-4");
    expect(within(table).getByText("가장 저렴")).toBeInTheDocument();
    expect(within(table).getByText("가장 비쌈")).toBeInTheDocument();
    expect(within(table).getByText("입력 27.2만 토큰 초과 호출은 장문 요금")).toBeInTheDocument();
    expect(within(table).getAllByText("쓰기 $5.00")).toHaveLength(4); // Anthropic 만 캐시 쓰기 요금
  });

  it("정렬·최저/최고 계산은 시나리오를 따른다", () => {
    const models = costs().models;
    expect(sortModels(models, "projection", "asc", "2026-11", "nocache")[0].id).toBe("google-11");
    expect(sortModels(models, "name", "asc", "2026-11", "cache")[0].name).toBe("Claude 모델0");
    expect(extremes(models, "cache")).toEqual({ cheapest: "google-11", priciest: "openai-4" });
    const flat = models.map((m) => ({ ...m, projection: { cache: 0, nocache: 0 } }));
    expect(extremes(flat, "cache")).toEqual({ cheapest: null, priciest: null });
  });
});

describe("메뉴·설정 링크", () => {
  it("LLM 비용은 설정에 속한다: 사이드바에는 없고, LLM 비용 화면에서는 오른쪽 위 ⚙️ 설정이 켜져 보인다", () => {
    mockFetch({});
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route path="costs" element={<div>비용 본문</div>} />
        </Route>
      </Routes>,
      { route: "/costs" },
    );
    expect(screen.queryByRole("link", { name: "LLM 비용" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("aria-current", "page");
    expect(isActive(SETTINGS, "/costs")).toBe(true);
  });

  it("설정 화면에 LLM 비용 예상 링크가 있다", () => {
    mockFetch({});
    renderWithProviders(<Settings />);
    expect(screen.getByRole("link", { name: /LLM 비용 예상/ })).toHaveAttribute("href", "/costs");
  });
});
