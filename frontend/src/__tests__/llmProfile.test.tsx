import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import type { LlmCosts, LlmModelOption, LlmProfileName, LlmProfiles } from "../api/types";
import { Layout } from "../components/Layout";
import { AgentPage } from "../pages/Agent";
import { Costs } from "../pages/Costs";
import { LlmProfileSettings } from "../pages/settings/LlmSection";
import { fmtUsd, llmChipText, modelOptionText } from "../utils/llm";
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
    expect(within(dialog).getByRole("link", { name: /LLM 요금 보기/ })).toHaveAttribute("href", "/costs");
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

// ── 유료 모델 고르기 (profiles.paid.models, PUT /api/llm/model) ──

const opt = (
  id: string,
  name: string,
  provider: LlmModelOption["provider"],
  over: Partial<LlmModelOption> = {},
): LlmModelOption => ({
  id,
  name,
  provider,
  tier: "주력",
  input: 2,
  output: 10,
  note: "",
  available: provider === "openai",
  reason: provider === "openai" ? null : "Google 키가 .env 에 없어요",
  selected: false,
  ...over,
});

const MODELS: LlmModelOption[] = [
  opt("gpt-top", "GPT Top", "openai", { tier: "최상위", input: 15, output: 60 }),
  opt("gpt-main", "GPT Main", "openai", { selected: true }),
  opt("gpt-mini", "GPT Mini", "openai", { tier: "경량", input: 0.1, output: 0.4 }),
  opt("gemini-pro", "Gemini Pro", "google"),
  opt("my-model", "my-model (.env)", "env", {
    tier: "",
    input: null,
    output: null,
    available: false,
    reason: null,
  }),
];

const withModels = (active: LlmProfileName = "paid", models = MODELS): LlmProfiles => {
  const base = profiles(active);
  return {
    ...base,
    profiles: {
      ...base.profiles,
      paid: {
        ...base.profiles.paid,
        model: models.find((m) => m.selected)?.id ?? null,
        provider: "openai",
        provider_label: "OpenAI",
        env_model: "my-model",
        models,
      },
    },
  };
};

/** /api/llm/profile + /api/llm/model 가짜 서버 */
function mockModelApi(initial: LlmProfiles, onPut?: (model: string) => Response | undefined) {
  const puts: unknown[] = [];
  let state = initial;
  const fetchMock = mockFetch({
    "/api/system": SYSTEM,
    "/api/llm/profile": () => state,
    "/api/llm/model": (_url: string, init?: RequestInit) => {
      const body = JSON.parse(init!.body as string);
      puts.push({ method: init?.method, body });
      const err = onPut?.(body.model);
      if (err) return err;
      const models = state.profiles.paid.models!.map((m) => ({ ...m, selected: m.id === body.model }));
      state = withModels(state.active, models);
      return state;
    },
  });
  return { fetchMock, puts };
}

const modelSelect = () => screen.findByRole("combobox", { name: "유료 모델" });

describe("유료 모델 고르기 (설정)", () => {
  it("제공사와 제공사별 묶음을 보여 주고, 감지되지 않은 제공사 모델은 고를 수 없다", async () => {
    mockModelApi(withModels());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    expect(screen.getByText(/제공사:/)).toHaveTextContent("제공사: OpenAI (.env 키로 감지)");
    expect(select).toHaveValue("gpt-main");

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups).toEqual(["OpenAI", "Google", ".env"]); // Anthropic 은 쓰지 않는다 (D-66)

    const option = (name: RegExp) => within(select).getByRole("option", { name });
    expect(option(/GPT Main/)).toHaveTextContent("GPT Main · 주력 · 입력 $2 / 출력 $10");
    expect(option(/GPT Mini/)).toHaveTextContent("GPT Mini · 경량 · 입력 $0.1 / 출력 $0.4");
    expect(option(/GPT Top/)).toBeEnabled();
    expect(option(/Gemini Pro/)).toBeDisabled();
    expect(option(/Gemini Pro/)).toHaveTextContent("— 키 필요");
    expect(option(/my-model \(\.env\)/)).toBeDisabled();
    // 못 쓰는 이유 전체 문장
    expect(screen.getByText("Google: Google 키가 .env 에 없어요")).toBeInTheDocument();
  });

  it("다른 OpenAI 모델을 고르면 확인 창을 거쳐 PUT /api/llm/model {model}", async () => {
    const { fetchMock, puts } = mockModelApi(withModels());
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.change(await modelSelect(), { target: { value: "gpt-mini" } });

    const dialog = screen.getByRole("dialog", { name: "유료 모델을 바꿀까요?" });
    expect(dialog).toHaveTextContent("호출마다 실제 요금이 나가요");
    expect(dialog).toHaveTextContent("GPT Mini");
    expect(dialog).toHaveTextContent("입력 $0.1 / 출력 $0.4 (100만 토큰당)");
    expect(dialog).toHaveTextContent("다음 판단·답장부터 적용돼요");
    expect(puts).toEqual([]);

    fireEvent.click(within(dialog).getByRole("button", { name: "이 모델로 바꾸기" }));
    await waitFor(() => expect(puts).toEqual([{ method: "PUT", body: { model: "gpt-mini" } }]));
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/llm/model")).toBe(true);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByText(/유료 모델을 바꿨어요 ✓ \(GPT Mini\)/)).toBeInTheDocument();
    expect(await modelSelect()).toHaveValue("gpt-mini");
  });

  it("확인 창에서 취소하면 보내지 않고 원래 모델로 돌아간다", async () => {
    const { puts } = mockModelApi(withModels());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    fireEvent.change(select, { target: { value: "gpt-top" } });
    expect(select).toHaveValue("gpt-top");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(select).toHaveValue("gpt-main");
    expect(puts).toEqual([]);
  });

  it("판단 세션 중(409)이면 서버 안내를 보여 주고 모델은 그대로", async () => {
    const detail = "판단 세션이나 답장이 진행 중이에요. 끝난 뒤에 바꿔 주세요";
    mockModelApi(withModels(), () => apiError(409, detail));
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    fireEvent.change(select, { target: { value: "gpt-mini" } });
    fireEvent.click(screen.getByRole("button", { name: "이 모델로 바꾸기" }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(new RegExp(`${detail} — 잠시 후 다시 시도해 주세요`))).toHaveClass(
      "error",
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "취소" }));
    expect(select).toHaveValue("gpt-main");
    expect(screen.getByText(new RegExp(detail))).toHaveClass("error");
  });

  it("제공사를 모르면 '알 수 없는 엔드포인트'", async () => {
    const data = withModels();
    data.profiles.paid.provider = null;
    data.profiles.paid.provider_label = null;
    mockModelApi(data);
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    expect(screen.getByText(/제공사:/)).toHaveTextContent("제공사: 알 수 없는 엔드포인트");
  });

  it("유료가 미설정이면 모델 목록을 그리지 않는다", async () => {
    const data = withModels("free");
    data.profiles.paid = { ...data.profiles.paid, ...UNCONFIGURED };
    mockModelApi(data);
    renderWithProviders(<LlmProfileSettings />);
    expect(await screen.findByRole("button", { name: /유료/ })).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: "유료 모델" })).not.toBeInTheDocument();
  });

  it("가격 글자 만들기", () => {
    expect(fmtUsd(0.1)).toBe("$0.1");
    expect(fmtUsd(2)).toBe("$2");
    expect(fmtUsd(1.25)).toBe("$1.25");
    expect(modelOptionText(opt("x", "X", "openai", { tier: "", input: null, output: null }))).toBe("X");
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
  it("유료면 '유료 · 모델' 이고 설정의 LLM 모델 화면으로 가는 링크", async () => {
    renderLayout({ configured: true, model: "paid-model-mini", profile: "paid", tool_mode: "auto" });
    const chip = await screen.findByTestId("llm-chip");
    expect(chip).toHaveTextContent("유료 · paid-model-mini");
    expect(chip).toHaveAttribute("href", "/settings/llm");
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
    today: "2026-11-05",
    months: ["2026-11"],
    month: "2026-11",
    groups: [],
    daily: [],
    current_model: "some-model",
    current_provider: profile === "paid" ? "openai" : "local",
    profile,
    usd_krw: null,
    strategies: {},
  });

  it("요금 화면: 지금 쓰는 모델과 무료/유료를 보여 준다", async () => {
    mockFetch({
      "/api/llm/costs/official": { month: "2026-11", window_tz: "UTC", providers: [] },
      "/api/llm/costs": costs("paid"),
    });
    renderWithProviders(<Costs />);
    expect(await screen.findByText("유료")).toHaveClass("pill", "paid");
    expect(screen.getByText("some-model")).toHaveClass("costs-model");
  });

  it("요금 화면: 무료면 무료 표시", async () => {
    mockFetch({
      "/api/llm/costs/official": { month: "2026-11", window_tz: "UTC", providers: [] },
      "/api/llm/costs": costs("free"),
    });
    renderWithProviders(<Costs />);
    expect(await screen.findByText("무료")).toHaveClass("pill");
    expect(screen.getByText("무료")).not.toHaveClass("paid");
  });
});
