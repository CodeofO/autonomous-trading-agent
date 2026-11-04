import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import type { LlmCosts, LlmProfileName, LlmProfiles } from "../api/types";
import { Layout } from "../components/Layout";
import { AgentPage } from "../pages/Agent";
import { Costs } from "../pages/Costs";
import { LlmProfileSettings } from "../pages/Settings";
import { llmChipText } from "../utils/llm";
import { apiError, mockFetch, renderWithProviders, SYSTEM } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const profiles = (
  active: LlmProfileName = "free",
  over: Partial<LlmProfiles["profiles"]> = {},
): LlmProfiles => ({
  active,
  profiles: {
    free: {
      label: "무료",
      env: "LLM_TEST_*",
      configured: true,
      model: "free-model-7b",
      api: "chat",
      reasoning_effort: null,
    },
    paid: {
      label: "유료",
      env: "LLM_*",
      configured: true,
      model: "paid-model-mini",
      api: "responses",
      reasoning_effort: "low",
    },
    ...over,
  },
});

const UNCONFIGURED = {
  configured: false,
  model: null,
  api: null,
  reasoning_effort: null,
} as const;

/** /api/llm/profile 가짜 서버 — PUT 은 handler 로 응답을 바꿀 수 있다 */
function mockProfileApi(initial: LlmProfiles, onPut?: (active: LlmProfileName) => Response | LlmProfiles) {
  const puts: unknown[] = [];
  let state = initial;
  const fetchMock = mockFetch({
    "/api/system": { ...SYSTEM, agent_interval_minutes: 15 },
    "/api/llm/profile": (_url: string, init?: RequestInit) => {
      if (init?.method !== "PUT") return state;
      const body = JSON.parse(init.body as string);
      puts.push(body);
      const r = onPut ? onPut(body.active) : { ...state, active: body.active };
      if (!(r instanceof Response)) state = r;
      return r;
    },
  });
  return { fetchMock, puts };
}

const seg = (name: RegExp) =>
  within(screen.getByRole("group", { name: "LLM 모델 선택" })).getByRole("button", { name });

describe("LLM 모델 카드 (설정)", () => {
  it("무료·유료 두 설정을 모델 이름·API 방식과 함께 보여준다", async () => {
    mockProfileApi(profiles("free"));
    renderWithProviders(<LlmProfileSettings />);
    expect(await screen.findByRole("heading", { name: "🤖 LLM 모델" })).toBeInTheDocument();
    const free = await screen.findByRole("button", { name: /무료/ });
    expect(free).toHaveTextContent("free-model-7b");
    expect(free).toHaveTextContent("Chat Completions");
    expect(free).toHaveAttribute("aria-pressed", "true");
    const paid = seg(/유료/);
    expect(paid).toHaveTextContent("paid-model-mini");
    expect(paid).toHaveTextContent("Responses API");
    expect(paid).toHaveAttribute("aria-pressed", "false");
    expect(paid).toBeEnabled();
  });

  it(".env 에 없는 설정은 누를 수 없고 채울 키를 알려 준다", async () => {
    mockProfileApi(profiles("free", { paid: { label: "유료", env: "LLM_*", ...UNCONFIGURED } }));
    renderWithProviders(<LlmProfileSettings />);
    const paid = await screen.findByRole("button", { name: /유료/ });
    expect(paid).toBeDisabled();
    expect(paid).toHaveTextContent("미설정 — .env 의 LLM_* 를 채우세요");
    expect(seg(/무료/)).toBeEnabled();
  });

  it("무료 설정이 비어 있으면 LLM_TEST_* 를 채우라고 한다", async () => {
    mockProfileApi(profiles("paid", { free: { label: "무료", env: "LLM_TEST_*", ...UNCONFIGURED } }));
    renderWithProviders(<LlmProfileSettings />);
    const free = await screen.findByRole("button", { name: /무료/ });
    expect(free).toBeDisabled();
    expect(free).toHaveTextContent("미설정 — .env 의 LLM_TEST_* 를 채우세요");
  });

  it("유료로 바꾸려면 확인 창을 거쳐야 하고, 확인하면 PUT {active:'paid'}", async () => {
    const { fetchMock, puts } = mockProfileApi(profiles("free"));
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /유료/ }));
    const dialog = screen.getByRole("dialog", { name: "유료 모델로 바꿀까요?" });
    expect(dialog).toHaveTextContent("유료 모델은 호출마다 실제 요금이 나가요.");
    expect(within(dialog).getByRole("link", { name: /LLM 비용 예상 보기/ })).toHaveAttribute(
      "href",
      "/costs",
    );
    await waitFor(() => expect(dialog).toHaveTextContent("장중 15분마다 판단"));
    expect(puts).toEqual([]);

    fireEvent.click(within(dialog).getByRole("button", { name: "유료로 바꾸기" }));
    await waitFor(() => expect(puts).toEqual([{ active: "paid" }]));
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT")!;
    expect(put[0]).toBe("/api/llm/profile");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText(/유료 모델\(paid-model-mini\)로 바꿨어요 ✓/)).toBeInTheDocument();
    expect(seg(/유료/)).toHaveAttribute("aria-pressed", "true");
  });

  it("확인 창에서 취소하면 아무것도 보내지 않는다", async () => {
    const { puts } = mockProfileApi(profiles("free"));
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /유료/ }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(puts).toEqual([]);
    expect(seg(/무료/)).toHaveAttribute("aria-pressed", "true");
  });

  it("무료로 바꿀 때는 확인 없이 바로 PUT {active:'free'}", async () => {
    const { puts } = mockProfileApi(profiles("paid"));
    renderWithProviders(<LlmProfileSettings />);
    expect(await screen.findByText(/유료 모델을 쓰는 중이에요/)).toBeInTheDocument();
    fireEvent.click(seg(/무료/));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(puts).toEqual([{ active: "free" }]));
    expect(await screen.findByText(/무료 모델\(free-model-7b\)로 바꿨어요 ✓/)).toBeInTheDocument();
  });

  it("지금 쓰는 쪽을 다시 누르면 아무것도 보내지 않는다", async () => {
    const { puts } = mockProfileApi(profiles("free"));
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /무료/ }));
    expect(puts).toEqual([]);
  });

  it("판단 세션 중(409)이면 서버 안내를 보여 주고 바꾸지 않는다", async () => {
    const detail = "판단 세션이나 답장이 진행 중이에요. 끝난 뒤에 바꿔 주세요";
    mockProfileApi(profiles("paid"), () => apiError(409, detail));
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /무료/ }));
    expect(await screen.findByText(new RegExp(`${detail} — 잠시 후 다시 시도해 주세요`))).toHaveClass(
      "error",
    );
    expect(seg(/유료/)).toHaveAttribute("aria-pressed", "true");
  });

  it(".env 미설정(400)이면 확인 창 안에 서버 안내를 보여 준다", async () => {
    const detail = ".env 에 LLM_* 를 채우고 다시 시작해야 쓸 수 있어요";
    mockProfileApi(profiles("free"), () => apiError(400, detail));
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: /유료/ }));
    fireEvent.click(screen.getByRole("button", { name: "유료로 바꾸기" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(detail)).toHaveClass("error");
    // 창을 닫으면 카드에 같은 안내가 남는다
    fireEvent.click(within(dialog).getByRole("button", { name: "취소" }));
    expect(screen.getByText(detail)).toHaveClass("error");
  });
});

function renderLayout(llm: typeof SYSTEM.llm | Record<string, unknown>) {
  mockFetch({ "/api/system": { ...SYSTEM, llm } });
  renderWithProviders(
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<div>본문</div>} />
      </Route>
    </Routes>,
  );
}

describe("상단 바 LLM 칩", () => {
  it("유료면 '유료 · 모델' 이고 설정 화면으로 가는 링크", async () => {
    renderLayout({ configured: true, model: "paid-model-mini", profile: "paid", tool_mode: "auto" });
    const chip = await screen.findByTestId("llm-chip");
    expect(chip).toHaveTextContent("유료 · paid-model-mini");
    expect(chip).toHaveAttribute("href", "/settings");
    expect(chip).toHaveClass("chip", "ok");
  });

  it("무료면 '무료 · 모델'", async () => {
    renderLayout({ configured: true, model: "free-model-7b", profile: "free", tool_mode: "auto" });
    expect(await screen.findByTestId("llm-chip")).toHaveTextContent("무료 · free-model-7b");
  });

  it("LLM 이 없으면 'LLM 미설정(규칙)' (warn)", async () => {
    renderLayout(SYSTEM.llm);
    const chip = await screen.findByTestId("llm-chip");
    expect(chip).toHaveTextContent("LLM 미설정(규칙)");
    expect(chip).toHaveClass("warn");
  });

  it("칩 글자 만들기", () => {
    expect(llmChipText({ configured: true, model: "m", profile: "paid" })).toBe("유료 · m");
    expect(llmChipText({ configured: true, model: "m", profile: "free" })).toBe("무료 · m");
    expect(llmChipText({ configured: false, model: null, profile: "free" })).toBe("LLM 미설정(규칙)");
  });
});

describe("다른 화면의 무료/유료 표시", () => {
  it("에이전트 구조: '유료 · 모델'", async () => {
    mockFetch({
      "/api/agent/structure": {
        persona: { name: "알파", emoji: "🦉" },
        llm: {
          configured: true,
          model: "paid-model-mini",
          profile: "paid",
          mode: "auto",
          max_steps: 8,
          max_tokens: 1024,
        },
        sources: [],
        tools: [],
        schedule: {
          interval_minutes: 15,
          morning: "08:35",
          sessions: ["09:15"],
          report: "15:45",
          monitor_minutes: 1,
          refresh_minutes: 10,
        },
        risk: {
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
        paper: { initial_cash: 10_000_000, commission_rate: 0.00015, sell_tax_rate: 0.002, slippage_bps: 5 },
        trading_mode: "paper",
        live_daily_buy_limit: null,
        engine_status: "RUNNING",
        agent_running: false,
      },
    });
    renderWithProviders(<AgentPage />);
    expect(await screen.findByText(/유료 · paid-model-mini · 도구 호출\(자동\)/)).toBeInTheDocument();
  });

  const costs = (profile: LlmProfileName): LlmCosts => ({
    checked: "2026-11-04",
    sources: {},
    current_model: "some-model",
    profile,
    usd_krw: null,
    months: [],
    models: [],
    projection: { month: "2026-11", remaining_trading_days: 10, basis_days: 0, basis_window_days: 30 },
  });

  it("비용 화면: 유료면 '요금 없음' 대신 실제 요금이 나간다고 말한다", async () => {
    mockFetch({ "/api/llm/costs": costs("paid") });
    renderWithProviders(<Costs />);
    expect(await screen.findByText("호출마다 실제 API 요금이 나가요")).toBeInTheDocument();
    expect(screen.getByText("유료")).toHaveClass("pill", "paid");
    expect(screen.queryByText("지금은 API 요금 없음")).not.toBeInTheDocument();
  });

  it("비용 화면: 무료면 '지금은 API 요금 없음' 그대로", async () => {
    mockFetch({ "/api/llm/costs": costs("free") });
    renderWithProviders(<Costs />);
    expect(await screen.findByText("지금은 API 요금 없음")).toBeInTheDocument();
    expect(screen.getByText("무료")).toHaveClass("pill");
  });
});
