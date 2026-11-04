import { act, screen, waitFor } from "@testing-library/react";
import { createChart } from "lightweight-charts";
import { Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMsg } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import { EquityChart } from "../components/charts";
import { Layout } from "../components/Layout";
import { RouteErrorBoundary } from "../components/RouteErrorBoundary";
import { lazyWithPreload, reloadForNewBuild } from "../lazyPage";
import { Portfolio } from "../pages/Portfolio";
import { Stock } from "../pages/Stock";
import { DashboardPage, OverviewPage, pageFor, StockPage } from "../routes";
import * as format from "../utils/format";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

// 화면 1단계 (docs/perf/REFACTOR_PLAN.md F2·F4·F5·F9): 다시 그리기·차트·코드 분할·화면 밀림

// 줄이 실제로 다시 그려졌는지 세려고 시각 형식 함수를 감싼다 (줄마다 한 번 부른다)
vi.mock("../utils/format", async (orig) => {
  const m = await orig<typeof import("../utils/format")>();
  return { ...m, fmtTime: vi.fn(m.fmtTime), fmtDateTime: vi.fn(m.fmtDateTime) };
});

afterEach(() => vi.unstubAllGlobals());

const persona = { name: "알파", emoji: "🦉" };
const divider = (id: number): ChatMsg => ({
  id,
  ts: `2026-11-04T10:${String(id).padStart(2, "0")}:00+09:00`,
  role: "system",
  kind: "text",
  content: `안내 ${id}`,
  run_id: null,
  payload: {},
});

describe("채팅 줄은 메모 — 새 메시지·진행 표시가 와도 이미 있던 줄은 다시 그리지 않는다", () => {
  beforeEach(() => vi.mocked(format.fmtTime).mockClear());

  it("메시지 하나가 붙으면 그 줄만, 입력 중 표시·같은 매니저(새 객체)는 0줄", () => {
    const five = [1, 2, 3, 4, 5].map(divider);
    const view = renderWithProviders(
      <ChatFeed messages={five} typing={null} persona={persona} showInput={false} />,
    );
    expect(screen.getAllByTestId("chat-system")).toHaveLength(5);
    expect(format.fmtTime).toHaveBeenCalledTimes(5);

    // 실시간 저장소처럼 이미 있던 메시지는 같은 객체를 그대로 쓴다
    const six = [...five, divider(6)];
    view.rerender(<ChatFeed messages={six} typing={null} persona={persona} showInput={false} />);
    expect(screen.getAllByTestId("chat-system")).toHaveLength(6);
    expect(format.fmtTime).toHaveBeenCalledTimes(6);

    view.rerender(
      <ChatFeed
        messages={six}
        typing={{ role: "analyst", label: "생각 중" }}
        persona={{ ...persona }}
        showInput={false}
      />,
    );
    expect(screen.getByTestId("typing")).toBeInTheDocument();
    expect(format.fmtTime).toHaveBeenCalledTimes(6);
  });

  it("세션 묶음: 다른 세션에 메시지가 오거나 진행 상태가 바뀌어도 지난 세션 블록은 그대로", () => {
    const fmt = vi.mocked(format.fmtDateTime);
    fmt.mockClear();
    vi.mocked(format.fmtTime).mockClear();
    const s1 = [
      { ...divider(1), role: "analyst" as const, run_id: 1, ts: "2026-11-02T09:00:00+09:00" },
      {
        ...divider(2),
        role: "analyst" as const,
        run_id: 1,
        kind: "summary" as const,
        ts: "2026-11-02T09:05:00+09:00",
      },
    ];
    const s2 = [{ ...divider(3), role: "analyst" as const, run_id: 2, ts: "2026-11-02T10:00:00+09:00" }];
    const headers = () => fmt.mock.calls.length + vi.mocked(format.fmtTime).mock.calls.length;
    const idle = { active: false, typing: null, replying: false };
    const view = renderWithProviders(
      <ChatFeed
        messages={[...s1, ...s2]}
        typing={null}
        progress={idle}
        persona={persona}
        showInput={false}
        groupSessions
      />,
    );
    expect(screen.getAllByTestId("session")).toHaveLength(2);
    const first = headers();
    expect(first).toBe(2); // 세션 머리줄마다 시작 시각 한 번

    const more = { ...divider(4), role: "analyst" as const, run_id: 2, ts: "2026-11-02T10:01:00+09:00" };
    const all = [...s1, ...s2, more];
    view.rerender(
      <ChatFeed
        messages={all}
        typing={null}
        progress={idle}
        persona={persona}
        showInput={false}
        groupSessions
      />,
    );
    expect(headers()).toBe(first + 1); // 세션 2만

    view.rerender(
      <ChatFeed
        messages={all}
        typing={null}
        progress={{ active: true, run_id: 2, typing: null, replying: false, label: "🔎 검색", step: 2 }}
        persona={persona}
        showInput={false}
        groupSessions
      />,
    );
    // 세션 2가 '진행 중'이 되어 다시 그려진다 — 세션 1은 그대로
    expect(headers()).toBe(first + 2);
    expect(screen.getByTestId("session-live")).toHaveTextContent("검색");
  });
});

describe("차트는 한 번 만들고 데이터만 바꾼다", () => {
  it("같은 내용의 새 배열이면 그대로, 내용이 바뀌면 setData 만", () => {
    vi.mocked(createChart).mockClear();
    const points = [
      { t: "2026-11-03T10:00:00+09:00", v: 100 },
      { t: "2026-11-03T10:10:00+09:00", v: 101 },
    ];
    const view = renderWithProviders(<EquityChart points={points} />);
    const chart = vi.mocked(createChart).mock.results[0].value as {
      addSeries: ReturnType<typeof vi.fn>;
    };
    const series = chart.addSeries.mock.results.at(-1)!.value as { setData: ReturnType<typeof vi.fn> };
    series.setData.mockClear();
    view.rerender(<EquityChart points={points.map((p) => ({ ...p }))} />);
    view.rerender(<EquityChart points={[...points]} />);
    expect(createChart).toHaveBeenCalledTimes(1);
    expect(series.setData).not.toHaveBeenCalled();

    view.rerender(<EquityChart points={[...points, { t: "2026-11-03T10:20:00+09:00", v: 102 }]} />);
    expect(createChart).toHaveBeenCalledTimes(1);
    expect(series.setData).toHaveBeenCalledTimes(1);
    expect(series.setData.mock.calls[0][0]).toHaveLength(3);
  });

  it("점이 없으면 차트를 만들지 않는다 (빈 자리만)", () => {
    vi.mocked(createChart).mockClear();
    renderWithProviders(<EquityChart points={[]} />);
    expect(screen.getByTestId("equity-chart")).toBeInTheDocument();
    expect(createChart).not.toHaveBeenCalled();
  });
});

describe("화면마다 따로 받는 조각", () => {
  it("주소·보기 → 화면 조각 ('/'는 전체면 한눈에 보기, 시장이면 대시보드)", () => {
    expect(pageFor("/", "ALL")).toBe(OverviewPage);
    expect(pageFor("/", "KR")).toBe(DashboardPage);
    expect(pageFor("/stock/005930", "US")).toBe(StockPage);
    expect(pageFor("/nowhere", "KR")).toBeNull();
  });

  it("미리 받기는 한 번만, 실패하면 다음에 다시 시도한다", async () => {
    const load = vi
      .fn<() => Promise<{ Page: () => null }>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ Page: () => null });
    const page = lazyWithPreload(load, (m) => m.Page);
    await expect(page.preload()).rejects.toThrow("offline");
    await page.preload();
    await page.preload();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("지난 배포의 조각을 못 받으면 (방금 새로고침했다면) 새로고침 안내를 보여 준다", async () => {
    // 방금 새로고침한 것으로 — 다시 새로고침하지 않고 오류 화면으로
    window.sessionStorage.setItem("chunk-reload-at", String(Date.now()));
    expect(reloadForNewBuild()).toBe(false);
    const Broken = lazyWithPreload(
      () => Promise.reject(new TypeError("Failed to fetch dynamically imported module")),
      () => () => null,
    );
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithProviders(
      <RouteErrorBoundary>
        <Broken />
      </RouteErrorBoundary>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("화면을 불러오지 못했어요");
    expect(screen.getByRole("button", { name: "새로고침" })).toBeInTheDocument();
    spy.mockRestore();
    window.sessionStorage.removeItem("chunk-reload-at");
  });
});

describe("데이터가 도착할 때 화면이 밀리지 않게", () => {
  it("상단 바: 상태를 받기 전에도 칩·엔진 상태·킬 스위치 자리를 잡아 둔다", async () => {
    let resolve: (v: unknown) => void = () => {};
    mockFetch({ "/api/system": () => new Promise((r) => (resolve = r)) });
    const { container } = renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>본문</div>} />
        </Route>
      </Routes>,
    );
    const top = container.querySelector(".topbar")!;
    expect(top.querySelectorAll(".top-skel")).toHaveLength(4);
    expect(top.querySelectorAll(".top-skel[aria-hidden]")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "킬 스위치" })).not.toBeInTheDocument();
    await act(async () => resolve(SYSTEM));
    await waitFor(() => expect(screen.getByTestId("engine-status")).toHaveTextContent("실행 중"));
    expect(top.querySelectorAll(".top-skel")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "킬 스위치" })).toBeInTheDocument();
  });

  it("포트폴리오: 받는 동안 '없어요'를 잠깐 보여 주지 않고 자리 표시 한 번, 다 받으면 한꺼번에", async () => {
    const gates: ((v: unknown) => void)[] = [];
    const later = (v: unknown) => () => new Promise((r) => gates.push(() => r(v)));
    mockFetch({
      "/api/portfolio": later({ equity: 1, positions: [] }),
      "/api/orders": later([]),
      "/api/fills": later([]),
      "/api/agent/decisions": later([]),
    });
    renderWithProviders(<Portfolio />);
    expect(screen.getByRole("status", { name: "포트폴리오 불러오는 중" })).toBeInTheDocument();
    expect(screen.queryByText(/보유 종목이 없어요/)).not.toBeInTheDocument();
    expect(screen.queryByText("아직 체결이 없어요.")).not.toBeInTheDocument();
    await waitFor(() => expect(gates).toHaveLength(4));
    await act(async () => gates.slice(0, 3).forEach((g) => g(undefined)));
    // 하나라도 남아 있으면 아직 자리 표시
    expect(screen.getByRole("status", { name: "포트폴리오 불러오는 중" })).toBeInTheDocument();
    await act(async () => gates[3](undefined));
    expect(await screen.findByText(/보유 종목이 없어요/)).toBeInTheDocument();
    expect(screen.getByText("아직 체결이 없어요.")).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "포트폴리오 불러오는 중" })).not.toBeInTheDocument();
  });

  it("종목: 일봉을 받는 동안 차트 자리를, 수급을 받는 동안 카드 자리를 잡아 둔다", () => {
    mockFetch({ "/api": () => new Promise(() => {}) });
    const { container } = renderWithProviders(
      <Routes>
        <Route path="stock/:symbol" element={<Stock />} />
      </Routes>,
      { route: "/stock/005930" },
    );
    expect(container.querySelector(".chart.chart-placeholder")).not.toBeNull();
    expect(container.querySelector(".supply-skel.kr")).not.toBeNull();
  });
});
