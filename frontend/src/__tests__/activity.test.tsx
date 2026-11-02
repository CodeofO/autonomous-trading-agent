import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentProgress, ChatMsg } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import { EventsProvider } from "../events";
import { Dashboard } from "../pages/Dashboard";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

// jsdom 에는 서버가 없으므로 웹소켓은 가짜로 — 테스트가 직접 열고 메시지를 밀어 넣는다
class FakeWS {
  static OPEN = 1;
  static instances: FakeWS[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  send() {}
  close() {}
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(channel: string, data: unknown) {
    this.onmessage?.({ data: JSON.stringify({ channel, data }) });
  }
}

const persona = { name: "알파", emoji: "🦉" };
const base = { ts: "2026-11-02T08:40:00+09:00", run_id: 7, payload: {} };
const tool: ChatMsg = { ...base, id: 1, role: "analyst", kind: "tool", content: "📰 뉴스 검색" };
const text: ChatMsg = { ...base, id: 2, role: "analyst", kind: "text", content: "뉴스부터 볼게요" };

const ACTIVE: AgentProgress = {
  active: true,
  run_id: 7,
  trigger: "manual",
  mode: "llm",
  started_at: new Date(Date.now() - 125_000).toISOString(),
  phase: "tool",
  label: "📰 가온전자 뉴스 검색",
  step: 3,
  max_steps: 8,
  tools: 5,
  sources: { 토스증권: 3, NAVER: 2 },
  tokens: 12_345,
  tool: "search_news",
  source: "NAVER",
  typing: "📰 가온전자 뉴스 검색",
  replying: false,
};
const IDLE: AgentProgress = { active: false, typing: null, replying: false };

// Node 의 내장 localStorage 가 jsdom 것을 가리는 환경이 있어 메모리 저장소로 바꿔 끼운다
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => vi.unstubAllGlobals());

describe("진행 표시 패널", () => {
  it("세션·단계·경과·도구·토큰·현재 활동·출처 칩을 보여준다", () => {
    renderWithProviders(
      <ChatFeed messages={[]} typing={null} progress={ACTIVE} persona={persona} showInput={false} />,
    );
    const panel = screen.getByTestId("activity");
    expect(panel).toHaveTextContent("세션 #7");
    expect(panel).toHaveTextContent("단계 3/8");
    expect(panel).toHaveTextContent(/02:0[5-9]/);
    expect(panel).toHaveTextContent("도구 5회");
    expect(panel).toHaveTextContent("12.3k 토큰");
    // 도구 라벨의 이모지가 아이콘 자리로 간다
    expect(panel.querySelector(".activity-icon")).toHaveTextContent("📰");
    expect(panel.querySelector(".activity-label")).toHaveTextContent(/^가온전자 뉴스 검색$/);
    expect(screen.getByText("토스증권")).toHaveTextContent("토스증권 3");
    expect(screen.getByText("NAVER")).toHaveClass("on");
  });

  it("세션이 없으면 숨기고, 답장 중에는 입력창 안내를 바꾼다", () => {
    renderWithProviders(
      <ChatFeed messages={[]} typing={null} progress={{ ...IDLE, replying: true }} persona={persona} />,
    );
    expect(screen.queryByTestId("activity")).not.toBeInTheDocument();
    const input = screen.getByRole("textbox", { name: "메시지 입력" });
    expect(input).toHaveAttribute("placeholder", expect.stringContaining("답장 쓰는 중이에요"));
    fireEvent.change(input, { target: { value: "하나 더" } });
    expect(screen.getByRole("button", { name: "보내기" })).toBeEnabled();
  });
});

describe("새로고침·재접속 복원", () => {
  it("열자마자 서버의 진행 상태와 입력 중 표시를 불러오고, 재접속하면 다시 맞춘다", async () => {
    const fetchMock = mockFetch({
      "/api/agent/progress": ACTIVE,
      "/api/chat": [tool, text],
      "/api/system": SYSTEM,
    });
    renderWithProviders(
      <EventsProvider>
        <Dashboard />
      </EventsProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("activity")).toHaveTextContent("세션 #7"));
    expect(screen.getByTestId("typing")).toHaveTextContent("가온전자 뉴스 검색");
    const calls = () => fetchMock.mock.calls.filter(([u]) => String(u) === "/api/agent/progress").length;
    expect(calls()).toBe(1);

    const ws = FakeWS.instances[0];
    act(() => ws.open());
    await waitFor(() => expect(calls()).toBe(2));

    act(() => ws.push("progress", IDLE));
    expect(screen.queryByTestId("activity")).not.toBeInTheDocument();
    expect(screen.queryByTestId("typing")).not.toBeInTheDocument();
  });

  it("대시보드의 '리서치 과정 보기'를 끄면 도구 메시지를 숨기고 설정을 기억한다", async () => {
    mockFetch({ "/api/agent/progress": IDLE, "/api/chat": [tool, text], "/api/system": SYSTEM });
    renderWithProviders(
      <EventsProvider>
        <Dashboard />
      </EventsProvider>,
    );
    await waitFor(() => expect(screen.getByText("뉴스부터 볼게요")).toBeInTheDocument());
    expect(screen.getByText("📰 뉴스 검색")).toBeInTheDocument();
    const toggle = screen.getByRole("checkbox", { name: "리서치 과정 보기" });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(screen.queryByText("📰 뉴스 검색")).not.toBeInTheDocument();
    expect(screen.getByText("뉴스부터 볼게요")).toBeInTheDocument();
    expect(window.localStorage.getItem("chat.showTools")).toBe("false");
  });
});
