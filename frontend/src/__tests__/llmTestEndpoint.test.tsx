import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LlmTestCheck, LlmTestEndpoint, LlmTestValues } from "../api/types";
import { LlmProfileSettings, TestEndpointSettings } from "../pages/settings/LlmSection";
import { apiError, mockFetch, renderWithProviders, SYSTEM } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const ENV: LlmTestValues = {
  base_url: "http://env-host:8000/v1",
  model: "env-model",
  api: "auto",
  reasoning_effort: "",
};

/** 서버 응답 모양: over 로 준 칸은 '화면', 나머지는 .env */
function endpoint(over: Partial<LlmTestValues> = {}, extra: Partial<LlmTestEndpoint> = {}): LlmTestEndpoint {
  const v = { ...ENV, ...over };
  return {
    ...v,
    source: {
      base_url: "base_url" in over ? "화면" : ".env",
      model: "model" in over ? "화면" : ".env",
      api: "api" in over ? "화면" : ".env",
      reasoning_effort: "reasoning_effort" in over ? "화면" : ".env",
    },
    env: ENV,
    has_key: false,
    configured: !!(v.base_url && v.model),
    ...extra,
  };
}

type Call = { url: string; method: string; body: unknown };

/** /api/llm/test-endpoint 가짜 서버 (models·check 는 더 긴 경로라 먼저 둔다) */
function mockTestApi(
  initial: LlmTestEndpoint,
  opts: {
    models?: (url: string) => unknown;
    check?: () => unknown;
    put?: (body: LlmTestValues) => Response | undefined;
  } = {},
) {
  const calls: Call[] = [];
  let state = initial;
  const fetchMock = mockFetch({
    "/api/llm/test-endpoint/models": (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: null });
      return opts.models ? opts.models(url) : { models: ["m-a", "m-b"] };
    },
    "/api/llm/test-endpoint/check": (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: null });
      return opts.check ? opts.check() : apiError(500, "no check");
    },
    "/api/llm/test-endpoint": (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ url, method, body });
      if (method === "PUT") {
        const err = opts.put?.(body);
        if (err) return err;
        state = endpoint(body);
      } else if (method === "DELETE") {
        state = endpoint();
      }
      return state;
    },
    "/api/llm/profile": {
      active: "free",
      profiles: {
        free: {
          label: "무료",
          env: "LLM_TEST_*",
          configured: true,
          model: "env-model",
          api: "chat",
          reasoning_effort: null,
        },
        paid: {
          label: "유료",
          env: "LLM_*",
          configured: false,
          model: null,
          api: null,
          reasoning_effort: null,
        },
      },
    },
    "/api/system": SYSTEM,
  });
  const writes = () => calls.filter((c) => c.method !== "GET");
  return { fetchMock, calls, writes };
}

const field = (name: string) => screen.findByLabelText(name);
const section = () => screen.getByRole("group", { name: "테스트용(무료) LLM" });

describe("테스트용(무료) LLM 지정 (설정)", () => {
  it("지금 값과 칸별 출처(화면/.env)·키 상태를 보여 준다", async () => {
    mockTestApi(endpoint({ model: "screen-model", reasoning_effort: "low" }));
    renderWithProviders(<TestEndpointSettings />);

    expect(await field("주소")).toHaveValue("http://env-host:8000/v1");
    expect(screen.getByLabelText("주소")).toHaveAttribute("placeholder", "http://서버:8000/v1");
    expect(screen.getByLabelText("모델")).toHaveValue("screen-model");
    expect(screen.getByLabelText("호출 방식")).toHaveValue("auto");
    expect(screen.getByLabelText("추론 강도")).toHaveValue("low");

    const badges = Array.from(section().querySelectorAll(".llm-src")).map((b) => b.textContent);
    expect(badges).toEqual([".env", "화면에서 지정", ".env", "화면에서 지정"]);
    // 화면에서 지정한 칸은 .env 기본값도 같이 보여 준다
    expect(screen.getByText(".env 값: env-model")).toBeInTheDocument();
    expect(screen.getByText(".env 값: (비어 있음)")).toBeInTheDocument();

    expect(
      screen.getByText("키 없음 — 키를 받지 않는 서버면 그대로 쓸 수 있어요 (키는 .env 에만 넣어요)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("여기서 바꾼 값은 앱에 저장돼 재시작해도 유지돼요. 키는 화면에서 받지 않아요."),
    ).toBeInTheDocument();
    // 화면에서 지정한 값이 있으니 되돌리기를 보여 준다
    expect(screen.getByRole("button", { name: ".env 값으로 되돌리기" })).toBeEnabled();
    // 키 입력 칸은 없다
    expect(section().querySelector('input[type="password"]')).toBeNull();
  });

  it(".env 값만 쓰면 되돌리기를 숨기고, 키가 있으면 .env 키를 쓴다고 알려 준다", async () => {
    mockTestApi(endpoint({}, { has_key: true }));
    renderWithProviders(<TestEndpointSettings />);
    expect(await screen.findByText("키: .env 의 LLM_TEST_API_KEY 사용")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ".env 값으로 되돌리기" })).not.toBeInTheDocument();
    // 바꾼 게 없으면 저장할 수 없다
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("고친 뒤 저장하면 네 칸을 PUT 하고 출처가 '화면에서 지정'으로 바뀐다", async () => {
    const { writes } = mockTestApi(endpoint());
    renderWithProviders(<TestEndpointSettings />);
    fireEvent.change(await field("주소"), { target: { value: "http://10.0.0.5:8000/v1" } });
    fireEvent.change(screen.getByLabelText("모델"), { target: { value: "new-model" } });
    fireEvent.change(screen.getByLabelText("호출 방식"), { target: { value: "responses" } });
    fireEvent.change(screen.getByLabelText("추론 강도"), { target: { value: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(writes()).toEqual([
        {
          url: "/api/llm/test-endpoint",
          method: "PUT",
          body: {
            base_url: "http://10.0.0.5:8000/v1",
            model: "new-model",
            api: "responses",
            reasoning_effort: "high",
          },
        },
      ]),
    );
    expect(await screen.findByText(/저장했어요 ✓/)).toHaveClass("ok");
    const badges = Array.from(section().querySelectorAll(".llm-src")).map((b) => b.textContent);
    expect(badges).toEqual(["화면에서 지정", "화면에서 지정", "화면에서 지정", "화면에서 지정"]);
    expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  });

  it("판단 중(409)이면 서버 안내를 보여 준다", async () => {
    const detail = "판단 세션이나 답장이 진행 중이에요. 끝난 뒤에 바꿔 주세요";
    mockTestApi(endpoint(), { put: () => apiError(409, detail) });
    renderWithProviders(<TestEndpointSettings />);
    fireEvent.change(await field("모델"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(new RegExp(`${detail} — 잠시 후 다시 시도해 주세요`))).toHaveClass(
      "error",
    );
    // 고친 값은 그대로 남는다
    expect(screen.getByLabelText("모델")).toHaveValue("x");
  });

  it(".env 값으로 되돌리기는 DELETE 를 보내고 .env 값을 다시 보여 준다", async () => {
    const { writes } = mockTestApi(endpoint({ model: "screen-model" }));
    renderWithProviders(<TestEndpointSettings />);
    expect(await field("모델")).toHaveValue("screen-model");
    fireEvent.click(screen.getByRole("button", { name: ".env 값으로 되돌리기" }));
    await waitFor(() =>
      expect(writes()).toEqual([{ url: "/api/llm/test-endpoint", method: "DELETE", body: null }]),
    );
    await waitFor(() => expect(screen.getByLabelText("모델")).toHaveValue("env-model"));
    expect(screen.getByText(".env 값으로 되돌렸어요 ✓")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ".env 값으로 되돌리기" })).not.toBeInTheDocument();
  });

  it("목록 불러오기는 지금 적은 주소로 모델 목록을 받아 고를 수 있게 한다", async () => {
    const { calls } = mockTestApi(endpoint());
    renderWithProviders(<TestEndpointSettings />);
    fireEvent.change(await field("주소"), { target: { value: "http://typed:9000/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "목록 불러오기" }));

    const list = await screen.findByRole("combobox", { name: "불러온 모델" });
    const models = calls.find((c) => c.url.startsWith("/api/llm/test-endpoint/models"))!;
    expect(models.method).toBe("POST");
    expect(models.url).toBe(
      `/api/llm/test-endpoint/models?base_url=${encodeURIComponent("http://typed:9000/v1")}`,
    );
    expect(
      within(list)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["불러온 모델 2개 중에서 고르기", "m-a", "m-b"]);
    fireEvent.change(list, { target: { value: "m-b" } });
    expect(screen.getByLabelText("모델")).toHaveValue("m-b");
    expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
  });

  it("모델 목록을 못 받으면(502) 서버 안내를 보여 준다", async () => {
    mockTestApi(endpoint(), { models: () => apiError(502, "모델 목록을 못 받았어요 (HTTP 404)") });
    renderWithProviders(<TestEndpointSettings />);
    await field("주소");
    fireEvent.click(screen.getByRole("button", { name: "목록 불러오기" }));
    expect(await screen.findByText("모델 목록을 못 받았어요 (HTTP 404)")).toHaveClass("error");
  });

  it("연결 확인은 진행 표시 뒤 4단계 결과를 ✅/❌ 와 함께 보여 준다", async () => {
    let finish: (r: LlmTestCheck) => void = () => {};
    const pending = new Promise<LlmTestCheck>((r) => (finish = r));
    const { fetchMock } = mockTestApi(endpoint());
    // check 만 늦게 끝나도록 감싼다
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).startsWith("/api/llm/test-endpoint/check")) {
        expect(init?.method).toBe("POST");
        return new Response(JSON.stringify(await pending), { status: 200 });
      }
      return base(input, init);
    });
    renderWithProviders(<TestEndpointSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "연결 확인 (무료)" }));
    expect(await screen.findByText(/짧은 요청 4개를 보내는 중/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "연결 확인 (무료)" })).toBeDisabled();

    finish({
      ok: false,
      model: "env-model",
      api: "chat",
      steps: [
        { label: "1. 기본 응답", ok: true, detail: "pong (0.8s)" },
        { label: "2. 도구 호출", ok: true, detail: "get_portfolio" },
        { label: "3. JSON 응답", ok: true, detail: "{...}" },
        { label: "4. 긴 입력", ok: false, detail: "HTTP 500" },
      ],
    });
    const result = await screen.findByLabelText("연결 확인 결과");
    expect(screen.queryByText(/짧은 요청 4개를 보내는 중/)).not.toBeInTheDocument();
    expect(result).toHaveTextContent("일부 확인에 실패했어요");
    expect(result).toHaveTextContent("env-model · Chat Completions");
    const items = within(result).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("✅ 1. 기본 응답");
    expect(items[3]).toHaveTextContent("❌ 4. 긴 입력");
    expect(within(items[3]).getByText("HTTP 500").tagName).toBe("CODE");
  });

  it("미설정이면 연결 확인을 누를 수 없다", async () => {
    mockTestApi(endpoint({ base_url: "", model: "" }));
    renderWithProviders(<TestEndpointSettings />);
    expect(await screen.findByRole("button", { name: "연결 확인 (무료)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "목록 불러오기" })).toBeDisabled();
  });

  it("LLM 모델 카드 안에 들어가고, 예전 서버(404)면 숨긴다", async () => {
    mockTestApi(endpoint());
    const { unmount } = renderWithProviders(<LlmProfileSettings />);
    expect(await screen.findByRole("heading", { name: "테스트용(무료) LLM" })).toBeInTheDocument();
    unmount();
    vi.unstubAllGlobals();

    mockFetch({ "/api/system": SYSTEM });
    renderWithProviders(<TestEndpointSettings />);
    await waitFor(() => expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "테스트용(무료) LLM" })).not.toBeInTheDocument();
  });
});
