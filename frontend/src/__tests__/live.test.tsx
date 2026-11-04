import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuditEvent, ChatMsg } from "../api/types";
import { EventsProvider, LIVE_STALE_MS } from "../events";
import { useSystem } from "../hooks";
import { chatTargets, matchesTargets, stateTargets, targetsFor } from "../live/invalidation";
import { slowWhenLive, useAudit, useChat, useConnected, useLiveInterval } from "../live/hooks";
import { insertAudit, insertMessage, MAX_MESSAGES, mergeAudit, mergeMessages } from "../live/merge";
import { INVALIDATE_DELAY_MS, OPEN_GRACE_MS } from "../live/store";
import { BOTH_MARKETS, KR_MARKET, mockFetch, renderWithProviders, SYSTEM } from "./utils";

// 화면 1단계 (docs/perf/REFACTOR_PLAN.md F3·F6·F7·F8·F10): 실시간 저장소의 계약을 고정한다

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
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
  push(channel: string, data: unknown) {
    this.onmessage?.({ data: JSON.stringify({ channel, data }) });
  }
}

const krWs = () => FakeWS.instances.find((w) => !w.url.includes("market=US"))!;
const usWs = () => FakeWS.instances.find((w) => w.url.includes("market=US"))!;

const msg = (id: number, over: Partial<ChatMsg> = {}): ChatMsg => ({
  id,
  ts: "2026-11-04T10:00:00+09:00",
  role: "analyst",
  kind: "text",
  content: `m${id}`,
  run_id: null,
  payload: {},
  ...over,
});
const audit = (id: number): AuditEvent => ({
  id,
  ts: "2026-11-04T10:00:00+09:00",
  type: "ORDER",
  symbol: null,
  message: `a${id}`,
  payload: {},
});
const IDLE = { active: false, typing: null, replying: false };
const sleep = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => vi.unstubAllGlobals());

describe("실시간 목록 합치기 (정렬된 채로 끼워 넣기)", () => {
  it("채팅: 끝에 붙이고, 늦게 온 메시지는 제자리에, 같은 id 는 새 것으로 바꾼다", () => {
    const a = [msg(1), msg(3), msg(5)];
    const appended = insertMessage(a, msg(6));
    expect(appended.map((m) => m.id)).toEqual([1, 3, 5, 6]);
    expect(appended[0]).toBe(a[0]); // 이미 있던 메시지는 같은 객체 (메모된 줄이 다시 그려지지 않는다)
    expect(insertMessage(a, msg(4)).map((m) => m.id)).toEqual([1, 3, 4, 5]);
    expect(insertMessage(a, msg(0)).map((m) => m.id)).toEqual([0, 1, 3, 5]);
    const replaced = insertMessage(a, msg(3, { content: "new" }));
    expect(replaced.map((m) => m.content)).toEqual(["m1", "new", "m5"]);
    expect(insertMessage(a, a[1])).toBe(a); // 같은 객체면 그대로
    expect(a.map((m) => m.id)).toEqual([1, 3, 5]); // 원래 배열은 건드리지 않는다
  });

  it("채팅: 최근 800개까지만", () => {
    const full = Array.from({ length: MAX_MESSAGES }, (_, i) => msg(i + 1));
    const next = insertMessage(full, msg(MAX_MESSAGES + 1));
    expect(next).toHaveLength(MAX_MESSAGES);
    expect(next[0].id).toBe(2);
    expect(next.at(-1)!.id).toBe(MAX_MESSAGES + 1);
  });

  it("받아 온 기록과 합칠 때 이미 있던 것은 그대로 두고, 새 것이 없으면 같은 배열", () => {
    const cur = [msg(3), msg(5)];
    const merged = mergeMessages(cur, [msg(1), msg(2), msg(3, { content: "server" })]);
    expect(merged.map((m) => m.id)).toEqual([1, 2, 3, 5]);
    expect(merged[2]).toBe(cur[0]); // 실시간으로 받은 것이 남는다
    expect(mergeMessages(cur, [msg(3), msg(5)])).toBe(cur);
    expect(mergeMessages([], [msg(2), msg(1)]).map((m) => m.id)).toEqual([1, 2]); // 정렬 안 된 응답도
  });

  it("감사 로그: 최신 것부터, 겹치면 하나만", () => {
    const cur = [audit(5), audit(3)];
    expect(insertAudit(cur, audit(6)).map((e) => e.id)).toEqual([6, 5, 3]);
    expect(insertAudit(cur, audit(4)).map((e) => e.id)).toEqual([5, 4, 3]);
    expect(insertAudit(cur, audit(5)).map((e) => e.id)).toEqual([5, 3]);
    expect(mergeAudit(cur, [audit(7), audit(5), audit(1)]).map((e) => e.id)).toEqual([7, 5, 3, 1]);
  });
});

describe("이벤트 → 다시 받을 캐시 (전부 다시 받지 않는다)", () => {
  it("채팅 종류별", () => {
    expect(chatTargets(msg(1, { role: "broker", kind: "fill" }))).toEqual({
      market: ["orders", "fills", "portfolio", "equity", "decisions", "candles"],
      global: ["overview"],
    });
    expect(chatTargets(msg(1, { role: "broker", kind: "text" }))?.market).toEqual([
      "orders",
      "portfolio",
      "decisions",
    ]);
    expect(chatTargets(msg(1, { kind: "decision" }))?.market).toEqual(["decisions", "candles"]);
    expect(chatTargets(msg(1, { kind: "summary", run_id: 3 }))?.market).toContain("runs");
    expect(chatTargets(msg(1, { role: "system", kind: "summary" }))?.market).toContain("equity");
    expect(chatTargets(msg(1, { role: "system", run_id: 3 }))?.market).toEqual(["runs"]);
    // 엔진·LLM·일정 변경 같은 시스템 안내는 그 시장 전부 + 공통 일부
    expect(chatTargets(msg(1, { role: "system" }))).toEqual({
      market: ["*"],
      global: ["overview", "markets", "llm-profile", "llm-costs", "schedule"],
    });
    expect(chatTargets(msg(1, { role: "user" }))?.market).toEqual(["agent-structure"]);
    // 매니저 생각·도구 기록·리스크 안내는 화면 데이터와 무관
    expect(chatTargets(msg(1, { kind: "text" }))).toBeNull();
    expect(chatTargets(msg(1, { kind: "tool" }))).toBeNull();
    expect(chatTargets(msg(1, { role: "risk", kind: "block" }))).toBeNull();
  });

  it("상태·진행·입력 중·감사 로그", () => {
    expect(stateTargets({ agent_running: true, run_id: 3 }).market).toEqual([
      "system",
      "runs",
      "agent-structure",
    ]);
    expect(stateTargets({ engine_status: "KILLED" }).global).toEqual(["overview", "markets"]);
    expect(stateTargets({ equity: 1, cash: 1 })).toEqual({
      market: ["portfolio", "equity"],
      global: ["overview"],
    });
    expect(targetsFor({ channel: "progress", data: IDLE })).toBeNull();
    expect(targetsFor({ channel: "typing", data: { role: "analyst", active: true, label: "" } })).toBeNull();
    expect(targetsFor({ channel: "audit", data: audit(1) })).toBeNull();
  });

  it("쿼리 키 고르기: 그 시장 것만, 시장이 붙은 공통 키도 그 시장 것만", () => {
    const t = { market: ["portfolio"], global: ["overview", "schedule"] };
    expect(matchesTargets(["KR", "portfolio"], "KR", t)).toBe(true);
    expect(matchesTargets(["US", "portfolio"], "KR", t)).toBe(false);
    expect(matchesTargets(["KR", "orders"], "KR", t)).toBe(false);
    expect(matchesTargets(["overview"], "KR", t)).toBe(true);
    expect(matchesTargets(["schedule", "KR"], "KR", t)).toBe(true);
    expect(matchesTargets(["schedule", "US"], "KR", t)).toBe(false);
    expect(matchesTargets(["llm-profile"], "KR", t)).toBe(false);
    expect(matchesTargets(["KR", "anything"], "KR", { market: ["*"], global: [] })).toBe(true);
  });

  it("체결 메시지가 오면 그 시장의 계좌·주문·체결·자산 추이와 한눈에 보기만 다시 받는다", async () => {
    mockFetch({});
    const { qc } = renderWithProviders(
      <EventsProvider>
        <div />
      </EventsProvider>,
      { markets: BOTH_MARKETS, market: "KR" },
    );
    const keys = [
      ["KR", "portfolio"],
      ["KR", "orders"],
      ["KR", "fills"],
      ["KR", "equity"],
      ["overview"],
      ["KR", "system"],
      ["KR", "runs"],
      ["US", "portfolio"],
      ["markets"],
      ["llm-profile"],
    ];
    for (const k of keys) if (k[0] !== "markets") qc.setQueryData(k, { ok: true }); // 시장 목록은 이미 들어 있다
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    act(() => krWs().open());
    act(() => krWs().push("chat", msg(10, { role: "broker", kind: "fill", payload: { symbol: "005930" } })));
    // 잦은 이벤트를 묶는 동안에는 아직 그대로
    expect(qc.getQueryState(["KR", "portfolio"])?.isInvalidated).toBe(false);
    await sleep(INVALIDATE_DELAY_MS + 50);
    const invalid = keys.filter((k) => qc.getQueryState(k)?.isInvalidated).map((k) => k.join("/"));
    expect(invalid).toEqual(["KR/portfolio", "KR/orders", "KR/fills", "KR/equity", "overview"]);
  });
});

/** 채팅을 보여 주는 화면처럼 */
function ChatProbe() {
  const { messages, progress } = useChat();
  return (
    <span data-testid="chat">
      {messages.length}:{progress ? "p" : "-"}
    </span>
  );
}
function AuditProbe() {
  return <span data-testid="audit">{useAudit().length}</span>;
}

describe("처음 받아 오는 것은 보여 주는 화면이 있을 때만, 연결마다 한 번", () => {
  const routes = () => ({
    "/api/chat": [msg(1), msg(2)],
    "/api/agent/progress": IDLE,
    "/api/events": [audit(2), audit(1)],
    "/api/us/chat": [],
    "/api/us/agent/progress": IDLE,
    "/api/us/events": [],
  });
  const calls = (fetchMock: ReturnType<typeof mockFetch>, prefix: string) =>
    fetchMock.mock.calls.filter(([u]) => String(u).startsWith(prefix)).length;

  it("채팅·감사 로그를 보여 주는 화면이 없으면 아무것도 받지 않는다", async () => {
    const fetchMock = mockFetch(routes());
    renderWithProviders(
      <EventsProvider>
        <div />
      </EventsProvider>,
      { markets: BOTH_MARKETS },
    );
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    act(() => FakeWS.instances.forEach((w) => w.open()));
    await sleep(OPEN_GRACE_MS + 50);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("웹소켓이 곧 열리면 열린 뒤 한 번만 받고, 화면을 다시 열어도 다시 받지 않는다", async () => {
    const fetchMock = mockFetch(routes());
    const view = renderWithProviders(
      <EventsProvider>
        <ChatProbe />
      </EventsProvider>,
    );
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    act(() => krWs().open()); // 기다리는 시간 안에 열림
    await waitFor(() => expect(screen.getByTestId("chat")).toHaveTextContent("2:p"));
    await sleep(OPEN_GRACE_MS + 50);
    expect(calls(fetchMock, "/api/chat")).toBe(1);
    expect(calls(fetchMock, "/api/agent/progress")).toBe(1);
    expect(calls(fetchMock, "/api/events")).toBe(0); // 감사 로그는 로그 화면에서만

    // 다른 화면에 갔다가 다시 와도 (실시간으로 이어 받고 있으므로) 다시 받지 않는다
    view.rerender(
      <EventsProvider>
        <div />
      </EventsProvider>,
    );
    view.rerender(
      <EventsProvider>
        <ChatProbe />
        <AuditProbe />
      </EventsProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("audit")).toHaveTextContent("2"));
    expect(calls(fetchMock, "/api/chat")).toBe(1);
    expect(calls(fetchMock, "/api/events")).toBe(1);
  });

  it("끊겼다 다시 열리면 놓친 것을 맞추려고 다시 받는다 (보여 주는 화면이 있을 때)", async () => {
    const fetchMock = mockFetch(routes());
    renderWithProviders(
      <EventsProvider>
        <ChatProbe />
      </EventsProvider>,
    );
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    act(() => krWs().open());
    await waitFor(() => expect(calls(fetchMock, "/api/chat")).toBe(1));
    act(() => krWs().drop());
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2), { timeout: 2000 }); // 다시 연결
    act(() => FakeWS.instances[1].open());
    await waitFor(() => expect(calls(fetchMock, "/api/chat")).toBe(2));
  });
});

/** 지금 시장 상태 폴링 주기와 연결 상태 */
function PollProbe() {
  useSystem();
  const live = useConnected();
  const every = useLiveInterval(undefined, 5000);
  return (
    <span data-testid="poll">
      {live ? "live" : "off"}:{every}
    </span>
  );
}

describe("웹소켓이 연결돼 있으면 폴링을 늦추고, 끊기면 다시 빠르게", () => {
  it("늦추는 주기: 5초 → 30초, 10초 → 60초, 30초 → 60초", () => {
    expect(slowWhenLive(5000)).toBe(30_000);
    expect(slowWhenLive(10_000)).toBe(60_000);
    expect(slowWhenLive(30_000)).toBe(60_000);
  });

  it("시장 상태(/api/system) 폴링: 연결 전 5초 · 연결 중 30초 · 끊기면 5초", async () => {
    mockFetch({ "/api/system": SYSTEM });
    const { qc } = renderWithProviders(
      <EventsProvider>
        <PollProbe />
      </EventsProvider>,
      { markets: [KR_MARKET] },
    );
    const interval = () =>
      qc.getQueryCache().find({ queryKey: ["KR", "system"] })?.observers[0]?.options.refetchInterval;
    expect(screen.getByTestId("poll")).toHaveTextContent("off:5000");
    expect(interval()).toBe(5000);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(1));
    act(() => krWs().open());
    expect(screen.getByTestId("poll")).toHaveTextContent("live:30000");
    expect(interval()).toBe(30_000);
    act(() => krWs().drop());
    expect(screen.getByTestId("poll")).toHaveTextContent("off:5000");
    expect(interval()).toBe(5000);
  });

  it("연결된 시장의 캐시는 30초 동안 '신선' — 화면을 옮길 때마다 다시 받지 않는다", async () => {
    mockFetch({});
    const view = renderWithProviders(
      <EventsProvider>
        <div />
      </EventsProvider>,
      { markets: BOTH_MARKETS },
    );
    const stale = (key: readonly unknown[]) => {
      const fn = view.qc.getDefaultOptions().queries?.staleTime;
      const q = view.qc.getQueryCache().build(view.qc, { queryKey: key });
      return typeof fn === "function" ? fn(q) : fn;
    };
    expect(stale(["KR", "orders"])).toBe(0);
    await waitFor(() => expect(FakeWS.instances).toHaveLength(2));
    act(() => krWs().open());
    expect(stale(["KR", "orders"])).toBe(LIVE_STALE_MS);
    expect(stale(["US", "orders"])).toBe(0); // 미장은 아직 연결 전
    expect(stale(["overview"])).toBe(LIVE_STALE_MS);
    act(() => usWs().open());
    expect(stale(["US", "orders"])).toBe(LIVE_STALE_MS);
    view.unmount();
    // 화면을 닫으면 원래 기본값으로
    expect(view.qc.getDefaultOptions().queries?.staleTime).toBeUndefined();
  });
});
