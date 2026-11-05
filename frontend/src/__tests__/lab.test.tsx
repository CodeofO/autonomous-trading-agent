import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LabRow, LabView } from "../api/types";
import { Lab } from "../pages/Lab";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const STYLES = [
  {
    id: "default",
    name: "기본 매니저",
    emoji: "🦉",
    persona: "알파",
    summary: "균형",
    tags: [],
    policy: null,
  },
  {
    id: "lynch",
    name: "피터 린치",
    emoji: "🧺",
    persona: "린치",
    summary: "GARP",
    tags: [],
    policy: "- PEG 1 이하",
  },
  {
    id: "buffett",
    name: "워런 버핏",
    emoji: "🏰",
    persona: "버핏",
    summary: "해자",
    tags: [],
    policy: "- 해자",
  },
];

const row = (id: string, kind: LabRow["kind"], ret: number, extra: Partial<LabRow> = {}): LabRow => {
  const s = STYLES.find((x) => x.id === id)!;
  return {
    id,
    kind,
    name: s.name,
    emoji: s.emoji,
    persona: s.persona,
    summary: s.summary,
    live: false,
    equity: 10_000_000 * (1 + ret / 100),
    cash: 1,
    initial_cash: 10_000_000,
    return_pct: ret,
    positions: 2,
    top_positions: [],
    invested_pct: 50,
    fills: 3,
    runs: 4,
    since: null,
    last_run: { id: 9, at: "2026-11-05T10:00:00+09:00", status: "DONE", summary: `${s.name} 요약` },
    running: false,
    ...extra,
  };
};

function view(over: Partial<LabView> = {}): LabView {
  return {
    market: "KR",
    currency: "KRW",
    live: false,
    main: "default",
    enabled: ["lynch", "buffett"],
    max_scenarios: 6,
    styles: STYLES,
    rows: [row("default", "main", 0.5), row("lynch", "scenario", -1.2), row("buffett", "scenario", 2.3)],
    round_running: false,
    running: null,
    llm: "free-model",
    next_run: null,
    ...over,
  };
}

describe("⚗️ 전략 실험실", () => {
  it("주 계좌를 맨 위에, 시나리오는 수익률 높은 순으로 비교한다", async () => {
    mockFetch({ "/api/lab": view() });
    renderWithProviders(<Lab />);
    const list = (await screen.findByRole("heading", { name: "📊 성과 비교" })).closest("section")!;
    const names = within(list)
      .getAllByRole("listitem")
      .filter((li) => li.classList.contains("lab-row"))
      .map((li) => li.getAttribute("aria-label"));
    expect(names).toEqual(["기본 매니저 주 계좌", "워런 버핏 시나리오", "피터 린치 시나리오"]);
    expect(screen.getByTestId("ret-scenario-lynch")).toHaveTextContent("-1.20%");
    expect(screen.getByTestId("ret-scenario-buffett")).toHaveTextContent("+2.30%");
    expect(screen.getAllByText("모의 시나리오")).toHaveLength(2);
  });

  it("시나리오 스위치는 켤 목록 전체를 PUT 하고, 지금 판단은 POST 로 시작한다", async () => {
    const puts: unknown[] = [];
    const posts: string[] = [];
    let state = view({ enabled: ["lynch"], rows: [row("default", "main", 0), row("lynch", "scenario", 0)] });
    mockFetch({
      "/api/lab/scenarios/lynch/run": (u: string) => (posts.push(u), { started: true }),
      "/api/lab/scenarios": (_u: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body)) as { enabled: string[] };
        puts.push(body);
        state = view({ enabled: body.enabled });
        return state;
      },
      "/api/lab": () => state,
    });
    renderWithProviders(<Lab />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "워런 버핏 시나리오" }));
    await waitFor(() => expect(puts).toEqual([{ enabled: ["lynch", "buffett"] }]));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "워런 버핏 시나리오" })).toBeChecked());
    const lynch = screen.getByRole("listitem", { name: "피터 린치 시나리오" });
    fireEvent.click(within(lynch).getByRole("button", { name: "▶ 지금 판단" }));
    await waitFor(() => expect(posts).toEqual(["/api/lab/scenarios/lynch/run"]));
  });

  it("주 계좌 전략은 고른 뒤 적용 — 실거래 계좌면 실제 주문이 나간다고 경고한다", async () => {
    const puts: unknown[] = [];
    mockFetch({
      "/api/lab/main": (_u: string, init?: RequestInit) => {
        puts.push(JSON.parse(String(init?.body)));
        return view({ live: true, main: "buffett" });
      },
      "/api/lab": view({ live: true, rows: [row("default", "main", 0, { live: true })] }),
    });
    renderWithProviders(<Lab />);
    expect(await screen.findByText("🔴 주 계좌 · 실거래")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "적용" });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /워런 버핏/ }));
    expect(screen.getByText(/이 전략으로 실제 주문이 나가요/)).toBeInTheDocument();
    fireEvent.click(apply);
    await waitFor(() => expect(puts).toEqual([{ id: "buffett" }]));
  });
});
