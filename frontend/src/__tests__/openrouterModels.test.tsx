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
  orm("mistralai/mistral-big", "Mistral Big", { selected: true, note: ".env" }),
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

const FRESH = {
  models_source: "openrouter" as const,
  models_loaded: true,
  models_stale: false,
  models_fetched_at: "2026-11-04T01:00:00Z",
  models_error: null,
};

const orProfile = (paid: Partial<LlmPaidProfileInfo> = {}, models = OR_MODELS): LlmProfiles => ({
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
      model: models.find((m) => m.selected)?.id ?? "mistralai/mistral-big",
      api: "chat",
      reasoning_effort: null,
      provider: "openrouter",
      provider_label: "OpenRouter",
      env_model: "mistralai/mistral-big",
      models,
      ...FRESH,
      ...paid,
    },
  },
});

/** GET /api/llm/profile + POST /api/llm/openrouter/models/refresh 가짜 서버 */
function mockOpenRouterApi(
  initial: LlmProfiles,
  onRefresh: (force: string | null) => LlmProfiles | Response | Promise<LlmProfiles | Response>,
) {
  let state = initial;
  const refreshes: { method?: string; force: string | null }[] = [];
  const fetchMock = mockFetch({
    "/api/system": SYSTEM,
    "/api/llm/profile": () => state,
  });
  // 새로고침은 응답을 늦출 수 있게(Promise) 따로 받는다 — mockFetch 핸들러는 값을 바로 돌려줘야 해서
  const inner = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    if (!url.startsWith("/api/llm/openrouter/models/refresh")) return inner(input, init);
    const force = new URL(url, "http://x").searchParams.get("force");
    refreshes.push({ method: init?.method, force });
    const r = await onRefresh(force);
    if (r instanceof Response) return r;
    state = r;
    return new Response(JSON.stringify(r), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return { fetchMock, refreshes };
}

const modelSelect = () => screen.findByRole("combobox", { name: "유료 모델" });
const optionTexts = (select: HTMLElement) =>
  Array.from(select.querySelectorAll("option")).map((o) => o.textContent);

describe("유료 모델 고르기 — OpenRouter", () => {
  it("만든 곳(group_label)별로 묶고, 요금·무료·문맥 길이를 한 줄에 보여 준다", async () => {
    const { refreshes } = mockOpenRouterApi(orProfile(), () => orProfile());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    expect(screen.getByText(/제공사:/)).toHaveTextContent("제공사: OpenRouter");
    expect(select).toHaveValue("mistralai/mistral-big");

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups).toEqual(["OpenAI", "Mistral", "DeepSeek"]);
    const option = (name: RegExp) => within(select).getByRole("option", { name });
    expect(option(/Mistral Big/)).toHaveTextContent(/^Mistral Big · 입력 \$3 \/ 출력 \$15 · 200K$/);
    expect(option(/GPT Main/)).toHaveTextContent(/^GPT Main · 입력 \$2 \/ 출력 \$10 · 128K$/);
    expect(option(/DeepSeek Chat/)).toHaveTextContent(
      /^DeepSeek Chat \(free\) · 입력 \$0 \/ 출력 \$0 · 무료 · 64K$/,
    );
    expect(option(/DeepSeek Mystery/)).toHaveTextContent(/^DeepSeek Mystery · 가격 정보 없음$/);

    expect(screen.getByText(/목록 갱신:/)).toHaveTextContent("5개");
    expect(screen.getByRole("button", { name: "목록 새로고침" })).toBeEnabled();
    // 새 목록이면 저절로 다시 받지 않는다
    await new Promise((r) => setTimeout(r, 20));
    expect(refreshes).toEqual([]);
  });

  it("목록이 오래됐으면(stale) 한 번만 저절로 받아 오고, 받는 동안 '목록 불러오는 중…'", async () => {
    let resolve!: (v: LlmProfiles) => void;
    const pending = new Promise<LlmProfiles>((r) => (resolve = r));
    const fresh = [...OR_MODELS, orm("google/gemini-new", "Gemini New")];
    const { refreshes } = mockOpenRouterApi(orProfile({ models_stale: true }), () => pending);
    renderWithProviders(<LlmProfileSettings />);

    expect(await screen.findByText("목록 불러오는 중…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "목록 새로고침" })).toBeDisabled();
    expect(refreshes).toEqual([{ method: "POST", force: "false" }]);

    resolve(orProfile({}, fresh));
    const select = await modelSelect();
    await waitFor(() =>
      expect(within(select).getByRole("option", { name: /Gemini New/ })).toBeInTheDocument(),
    );
    expect(screen.queryByText("목록 불러오는 중…")).not.toBeInTheDocument();
    expect(screen.getByText(/목록 갱신:/)).toHaveTextContent("6개");
    await new Promise((r) => setTimeout(r, 20));
    expect(refreshes).toHaveLength(1);
  });

  it("한 번도 받지 않은 목록(models_loaded=false)이면 비어 있어도 받아 와서 고르게 한다", async () => {
    const { refreshes } = mockOpenRouterApi(
      orProfile({ models_loaded: false, models_stale: true, models_fetched_at: null }, []),
      () => orProfile(),
    );
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    expect(select).toHaveValue("mistralai/mistral-big");
    expect(refreshes).toEqual([{ method: "POST", force: "false" }]);
  });

  it("'목록 새로고침' 은 force=true 로 다시 받는다", async () => {
    const { refreshes } = mockOpenRouterApi(orProfile(), () =>
      orProfile({}, [...OR_MODELS, orm("openai/gpt-new", "GPT New")]),
    );
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    fireEvent.click(screen.getByRole("button", { name: "목록 새로고침" }));
    await waitFor(() => expect(refreshes).toEqual([{ method: "POST", force: "true" }]));
    expect(await within(await modelSelect()).findByRole("option", { name: /GPT New/ })).toBeInTheDocument();
  });

  it("서버가 알려 준 목록 오류(models_error)를 보여 준다", async () => {
    mockOpenRouterApi(orProfile({ models_error: "OpenRouter 응답 503" }), () => orProfile());
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    expect(screen.getByText(/모델 목록을 받지 못했어요: OpenRouter 응답 503/)).toHaveClass("error");
  });

  it("새로고침이 400 이면 서버 안내를 보여 준다", async () => {
    const detail = "OpenRouter 가 아니에요";
    mockOpenRouterApi(orProfile(), () => apiError(400, detail));
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    fireEvent.click(screen.getByRole("button", { name: "목록 새로고침" }));
    expect(await screen.findByText(new RegExp(`모델 목록을 받지 못했어요: ${detail}`))).toHaveClass("error");
    // 목록은 그대로 남는다
    expect(within(await modelSelect()).getAllByRole("option")).toHaveLength(5);
  });

  it("검색 칸으로 목록을 좁히고, 지금 모델은 늘 남긴다", async () => {
    mockOpenRouterApi(orProfile(), () => orProfile());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    const search = screen.getByRole("searchbox", { name: "모델 검색" });

    fireEvent.change(search, { target: { value: "deepseek" } });
    expect(optionTexts(select)).toEqual([
      expect.stringMatching(/^Mistral Big/),
      expect.stringMatching(/^DeepSeek Chat/),
      expect.stringMatching(/^DeepSeek Mystery/),
    ]);
    expect(select).toHaveValue("mistralai/mistral-big");
    expect(screen.getByText("검색 결과 2개")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "GPT" } });
    expect(optionTexts(select)).toEqual([
      expect.stringMatching(/^GPT Main/),
      expect.stringMatching(/^Mistral Big/),
    ]);

    fireEvent.change(search, { target: { value: "없는모델" } });
    expect(optionTexts(select)).toEqual([expect.stringMatching(/^Mistral Big/)]);
    expect(screen.getByText(/검색 결과가 없어요/)).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "" } });
    expect(optionTexts(select)).toHaveLength(5);
  });

  it("검색해서 고른 모델로 PUT /api/llm/model 확인 창을 띄운다", async () => {
    mockOpenRouterApi(orProfile(), () => orProfile());
    renderWithProviders(<LlmProfileSettings />);
    const select = await modelSelect();
    fireEvent.change(screen.getByRole("searchbox", { name: "모델 검색" }), { target: { value: "mystery" } });
    fireEvent.change(select, { target: { value: "deepseek/ds-mystery" } });
    const dialog = screen.getByRole("dialog", { name: "유료 모델을 바꿀까요?" });
    expect(dialog).toHaveTextContent("DeepSeek Mystery");
    expect(dialog).toHaveTextContent("요금 정보가 없어요");
    expect(select).toHaveValue("deepseek/ds-mystery");
  });

  it("고정 목록(catalog)에는 검색 칸·새로고침이 없고 저절로 받지도 않는다", async () => {
    const data = orProfile({ provider: "openai", provider_label: "OpenAI", models_source: "catalog" }, [
      {
        ...orm("gpt-main", "GPT Main", { selected: true }),
        provider: "openai",
        group: undefined,
        group_label: undefined,
      },
    ]);
    const { refreshes } = mockOpenRouterApi(data, () => data);
    renderWithProviders(<LlmProfileSettings />);
    await modelSelect();
    expect(screen.queryByRole("searchbox", { name: "모델 검색" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "목록 새로고침" })).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(refreshes).toEqual([]);
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
    expect(filterModels(OR_MODELS, "mistral").map((x) => x.id)).toEqual([
      "mistralai/mistral-big",
      "mistralai/mistral-small",
    ]);
  });
});
