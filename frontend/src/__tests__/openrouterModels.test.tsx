import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LlmModelOption, LlmPaidProfileInfo, LlmProfiles } from "../api/types";
import { LlmProfileSettings } from "../pages/settings/LlmSection";
import { contextText, filterModels, groupModels, modelOptionText } from "../utils/llm";
import { apiError, mockFetch, renderWithProviders, SYSTEM } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const GROUP_LABEL: Record<string, string> = {
  mistralai: "Mistral",
  openai: "OpenAI",
  google: "Google",
  deepseek: "DeepSeek",
};

const orm = (id: string, name: string, over: Partial<LlmModelOption> = {}): LlmModelOption => {
  const group = id.split("/")[0];
  return {
    id,
    name,
    provider: "openrouter",
    group,
    group_label: GROUP_LABEL[group] ?? group,
    tier: "",
    input: 3,
    output: 15,
    context_length: 200000,
    note: "",
    available: true,
    reason: null,
    selected: false,
    ...over,
  };
};

const OR_MODELS: LlmModelOption[] = [
  orm("mistralai/mistral-small", "Mistral Small", { input: 1, output: 5 }),
  orm("openai/gpt-main", "GPT Main", { input: 2, output: 10, context_length: 131072 }),
  orm("deepseek/ds-chat:free", "DeepSeek Chat (free)", {
    input: 0,
    output: 0,
    note: "무료",
    context_length: 64000,
  }),
  orm("deepseek/ds-mystery", "DeepSeek Mystery", { input: null, output: null, context_length: null }),
];

/** 등록 모델(OpenRouter 묶음 하나) + OpenAI 가격표 — 화면이 보는 목록 (D-71) */
const ORS: LlmModelOption[] = OR_MODELS.map((m) => ({
  ...m,
  group: "openrouter",
  group_label: "OpenRouter",
}));
const SOL: LlmModelOption = {
  ...orm("gpt-6.1-sol", "GPT-6.1 Sol"),
  provider: "openai",
  group: undefined,
  group_label: undefined,
  tier: "주력",
  input: 2,
  output: 10,
  context_length: undefined,
  selected: true,
};
const MODELS = [SOL, ...ORS];

const FRESH = {
  models_source: "openrouter" as const,
  models_loaded: true,
  models_stale: false,
  models_fetched_at: "2026-11-04T01:00:00Z",
  models_error: null,
};

const orProfile = (paid: Partial<LlmPaidProfileInfo> = {}, models = MODELS): LlmProfiles => ({
  active: "paid",
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
      model: models.find((m) => m.selected)?.id ?? null,
      api: "responses",
      reasoning_effort: null,
      provider: "openai",
      provider_label: "OpenAI",
      env_model: "gpt-6.1-sol",
      models,
      providers: { openai: true, google: false, openrouter: true },
      openrouter_registered: models.filter((m) => m.provider === "openrouter").map((m) => m.id),
      ...FRESH,
      ...paid,
    },
  },
});

const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), { status, headers: { "Content-Type": "application/json" } });

/** GET /api/llm/profile + 새로고침·검색·등록·빼기·모델 바꾸기 가짜 서버 */
function mockOpenRouterApi(
  initial: LlmProfiles,
  onRefresh: (force: string | null) => LlmProfiles | Response | Promise<LlmProfiles | Response> = () =>
    initial,
) {
  let state = initial;
  const calls: { method?: string; url: string; body?: unknown }[] = [];
  const refreshes: { method?: string; force: string | null }[] = [];
  const fetchMock = mockFetch({
    "/api/system": SYSTEM,
    "/api/llm/profile": () => state,
  });
  const inner = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    if (url.startsWith("/api/llm/openrouter/models/refresh")) {
      const force = new URL(url, "http://x").searchParams.get("force");
      refreshes.push({ method: init?.method, force });
      const r = await onRefresh(force);
      if (r instanceof Response) return r;
      state = r;
      return json(r);
    }
    if (url.startsWith("/api/llm/openrouter/catalog")) {
      calls.push({ method: init?.method, url });
      return json({
        items: [
          {
            id: "qwen/qwen-next",
            name: "Qwen Next",
            group: "qwen",
            group_label: "Qwen",
            input: 0.1,
            output: 0.4,
            context_length: 262144,
            registered: false,
          },
          { ...OR_MODELS[0], registered: true },
        ],
        total: 300,
        error: null,
      });
    }
    if (url.startsWith("/api/llm/openrouter/registered")) {
      calls.push({ method: init?.method, url, body });
      const paid = state.profiles.paid;
      const add = init?.method === "POST" ? [orm(body.id, "Qwen Next", { group: "openrouter" })] : [];
      const gone = init?.method === "DELETE" ? new URL(url, "http://x").searchParams.get("id") : null;
      const models = [...paid.models!.filter((m) => m.id !== gone), ...add];
      state = orProfile({}, models);
      return json(state);
    }
    if (url === "/api/llm/model") {
      calls.push({ method: init?.method, url, body });
      const models = state.profiles.paid.models!.map((m) => ({
        ...m,
        selected: m.id === body.model && m.provider === body.provider,
      }));
      state = orProfile({}, models);
      return json(state);
    }
    return inner(input, init);
  });
  return { fetchMock, refreshes, calls };
}

const modelSelect = () => screen.findByRole("combobox", { name: "유료 모델" });

describe("유료 모델 고르기 — OpenAI·OpenRouter 키를 함께 (D-71)", () => {
  it("제공사 키 표시, OpenAI 가격표와 등록한 OpenRouter 모델을 한 목록에서", async () => {
    mockOpenRouterApi(orProfile());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    const keys = screen.getByRole("list", { name: "제공사 키" });
    expect(keys).toHaveTextContent("OpenAI 키 있음");
    expect(keys).toHaveTextContent("OpenRouter 키 있음");
    expect(keys).toHaveTextContent("Google 키 없음");
    expect(keys).toHaveTextContent("Qwen Cloud 키 없음 · .env QWEN_API_KEY"); // D-73
    expect(select).toHaveValue("openai|gpt-6.1-sol");
    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups).toEqual(["OpenAI", "OpenRouter"]);
    const option = (name: RegExp) => within(select).getByRole("option", { name });
    expect(option(/GPT Main/)).toHaveTextContent("GPT Main · 입력 $2 / 출력 $10 · 128K");
    expect(option(/DeepSeek Chat/)).toHaveTextContent("· 무료 · 64K");
    expect(option(/DeepSeek Mystery/)).toHaveTextContent("가격 정보 없음");
  });

  it("등록한 OpenRouter 모델을 고르면 확인 창을 거쳐 PUT {model, provider: openrouter}", async () => {
    const { calls } = mockOpenRouterApi(orProfile());
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.change(await modelSelect(), { target: { value: "openrouter|openai/gpt-main" } });
    const dialog = screen.getByRole("dialog", { name: "유료 모델을 바꿀까요?" });
    expect(dialog).toHaveTextContent("OpenRouter · GPT Main");
    expect(dialog).toHaveTextContent("지금: OpenAI · GPT-6.1 Sol");
    fireEvent.click(within(dialog).getByRole("button", { name: "이 모델로 바꾸기" }));
    await waitFor(() =>
      expect(calls).toEqual([
        { method: "PUT", url: "/api/llm/model", body: { model: "openai/gpt-main", provider: "openrouter" } },
      ]),
    );
    expect(await modelSelect()).toHaveValue("openrouter|openai/gpt-main");
  });

  it("검색해서 등록하면 목록에 생기고, 등록 모델은 빼기 (지금 쓰는 모델은 못 뺀다)", async () => {
    const { calls } = mockOpenRouterApi(orProfile());
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    const reg = screen.getByRole("region", { name: "OpenRouter 모델 등록" });
    fireEvent.change(within(reg).getByRole("searchbox", { name: "OpenRouter 모델 검색" }), {
      target: { value: "qwen" },
    });
    const add = await within(reg).findByRole("button", { name: "등록" });
    expect(within(reg).getByRole("button", { name: "등록됨" })).toBeDisabled();
    fireEvent.click(add);
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")).toEqual({
        method: "POST",
        url: "/api/llm/openrouter/registered",
        body: { id: "qwen/qwen-next" },
      }),
    );
    expect(await screen.findByText(/OpenRouter 모델을 등록했어요 ✓ \(qwen\/qwen-next\)/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(screen.getByRole("combobox", { name: "유료 모델" })).getByRole("option", {
          name: /Qwen Next/,
        }),
      ).toBeInTheDocument(),
    );

    // 빼기: 등록 목록의 Mistral Small 빼기
    const row = within(reg)
      .getAllByRole("listitem")
      .find((li) => li.textContent?.includes("mistralai/mistral-small"))!;
    fireEvent.click(within(row).getByRole("button", { name: "빼기" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("id=mistralai%2Fmistral-small"))).toBe(
        true,
      ),
    );
  });

  it("지금 쓰는 OpenRouter 모델은 빼기 버튼이 막혀 있다", async () => {
    const models = [{ ...SOL, selected: false }, ...ORS.map((m, i) => ({ ...m, selected: i === 0 }))];
    mockOpenRouterApi(orProfile({}, models));
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    const reg = screen.getByRole("region", { name: "OpenRouter 모델 등록" });
    const row = within(reg)
      .getAllByRole("listitem")
      .find((li) => li.textContent?.includes("mistralai/mistral-small"))!;
    expect(row).toHaveTextContent("지금 사용 중");
    expect(within(row).getByRole("button", { name: "빼기" })).toBeDisabled();
  });

  it("목록이 오래됐으면(stale) 한 번만 저절로 받아 오고, '목록 새로고침' 은 force=true", async () => {
    const { refreshes } = mockOpenRouterApi(orProfile({ models_stale: true }), () => orProfile());
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    await waitFor(() => expect(refreshes).toEqual([{ method: "POST", force: "false" }]));
    fireEvent.click(await screen.findByRole("button", { name: "목록 새로고침" }));
    await waitFor(() => expect(refreshes.at(-1)).toEqual({ method: "POST", force: "true" }));
  });

  it("OpenRouter 목록 오류를 보여 준다", async () => {
    mockOpenRouterApi(orProfile({ models_error: "OpenRouter 모델 목록 HTTP 500" }));
    renderWithProviders(<LlmProfileSettings />);
    expect(
      await screen.findByText(/OpenRouter 목록을 받지 못했어요: OpenRouter 모델 목록 HTTP 500/),
    ).toHaveClass("error");
  });

  it("OpenRouter 키가 없으면 등록 칸·새로고침이 없고 저절로 받지도 않는다", async () => {
    const { refreshes } = mockOpenRouterApi(
      orProfile({ providers: { openai: true, google: false, openrouter: false }, models_source: "catalog" }, [
        SOL,
      ]),
    );
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    expect(screen.queryByRole("region", { name: "OpenRouter 모델 등록" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "목록 새로고침" })).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "제공사 키" })).toHaveTextContent(
      "OpenRouter 키 없음 · .env OPENROUTER_API_KEY",
    );
    expect(refreshes).toEqual([]);
  });

  it("새로고침이 400 이면 서버 안내를 보여 준다", async () => {
    mockOpenRouterApi(orProfile(), () =>
      apiError(400, "OpenRouter 키가 .env 에 없어요 (OPENROUTER_API_KEY)"),
    );
    renderWithProviders(<LlmProfileSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "목록 새로고침" }));
    expect(await screen.findByText(/OPENROUTER_API_KEY/)).toHaveClass("error");
  });
});

describe("OpenRouter 목록 글자·묶기", () => {
  it("문맥 길이", () => {
    expect(contextText(200000)).toBe("200K");
    expect(contextText(131072)).toBe("128K");
    expect(contextText(1048576)).toBe("1M");
    expect(contextText(2000000)).toBe("2M");
    expect(contextText(8192)).toBe("8K");
  });

  it("요금이 없으면 '가격 정보 없음', 무료면 '무료'", () => {
    expect(modelOptionText(orm("x/a", "A", { input: null, output: null, context_length: null }))).toBe(
      "A · 가격 정보 없음",
    );
    expect(modelOptionText(orm("x/b", "B", { input: 0, output: 0, note: ".env · 무료" }))).toBe(
      "B · 입력 $0 / 출력 $0 · 무료 · 200K",
    );
    expect(
      modelOptionText(orm("x/c", "C", { available: false, reason: "OpenRouter 키가 .env 에 없어요" })),
    ).toBe("C · 입력 $3 / 출력 $15 · 200K — 키 필요");
  });

  it("group 이 없으면 provider 로 묶고 label 은 MODEL_GROUPS 이름", () => {
    const m = { ...orm("gpt", "GPT"), provider: "openai" as const, group: undefined, group_label: undefined };
    expect(groupModels([m]).map((g) => [g.key, g.label])).toEqual([["openai", "OpenAI"]]);
    expect(filterModels(OR_MODELS, "mistral").map((x) => x.id)).toEqual(["mistralai/mistral-small"]);
  });
});
