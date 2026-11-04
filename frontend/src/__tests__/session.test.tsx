import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentProgress, AgentRun, ChatMsg } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import { groupFeed, summarizeSession, toolCategory } from "../components/chat/sessions";
import { TradingRoom } from "../pages/TradingRoom";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

const persona = { name: "알파", emoji: "🦉" };
let nextId = 1;
const msg = (
  run_id: number | null,
  role: ChatMsg["role"],
  kind: ChatMsg["kind"],
  content: string,
  payload = {},
) => ({ id: nextId++, ts: "2026-11-02T08:40:00+09:00", run_id, role, kind, content, payload }) as ChatMsg;

const tool = (run: number, name: string, content: string, source: string, extra = {}) =>
  msg(run, "analyst", "tool", content, { tool: name, source, ok: true, line: `${content} 결과`, ...extra });
const decision = (run: number, action: "BUY" | "SELL" | "HOLD", name: string, quantity = 0) =>
  msg(run, "analyst", "decision", `${action} ${name}`, {
    symbol: `S${name}`,
    name,
    action,
    quantity,
    confidence: 0.7,
    rationale: `${name} 근거`,
    evidence: [],
  });

// 세션 #7: 끝난 예전 세션 / 세션 #8: 최신 세션 / 사이에 사용자 질문과 답장
const s7 = [
  msg(7, "system", "text", "☀️ 장 시작 전 점검 — 🦉 알파 매니저가 출근했어요. (세션 #7)", {
    trigger: "morning",
  }),
  tool(7, "get_portfolio", "💼 포트폴리오 확인", "모의계좌"),
  msg(7, "analyst", "text", "🌏 시장 관점: 반도체 강세"),
  decision(7, "BUY", "가온전자", 12),
  decision(7, "HOLD", "나래식품"),
  msg(7, "analyst", "summary", "가온전자 조금 샀어요", { counts: { BUY: 1, SELL: 0, HOLD: 1 } }),
];
const question = msg(null, "user", "text", "왜 샀어?");
const reply = msg(null, "analyst", "text", "실적 때문이에요", { reply_to: "user" });
const s8 = [
  msg(8, "system", "text", "⏰ 정시 점검 — 🦉 알파 매니저가 출근했어요. (세션 #8)", { trigger: "hourly" }),
  tool(8, "get_portfolio", "💼 포트폴리오 확인", "모의계좌"),
  tool(8, "get_price_history", "📈 가온전자 차트·지표 확인", "토스증권"),
  tool(8, "screen_stocks", "🧭 종목 스크리닝 (market_cap)", "토스증권"),
  tool(8, "get_supply_demand", "🧲 가온전자 수급 확인 (외국인·기관·공매도)", "토스증권"),
  tool(8, "get_financials", "🧾 가온전자 재무제표 분석", "DART"),
  tool(8, "search_news", "📰 뉴스 검색: “가온전자”", "NAVER"),
  tool(8, "search_news", "📰 뉴스 검색: “반도체”", "NAVER", { ok: false, preview: { error: "HTTP 500" } }),
  msg(8, "analyst", "text", "뉴스부터 볼게요"),
  msg(8, "analyst", "text", "🌏 시장 관점: 외국인 순매수 지속"),
  decision(8, "SELL", "다온건설", 5),
  decision(8, "HOLD", "가온전자"),
  msg(8, "risk", "block", "🛑 잠깐! 다온건설 매도 5주는 막을게요. 하루 한도"),
  msg(8, "analyst", "summary", "다온건설 정리하려 했어요", { counts: { BUY: 0, SELL: 1, HOLD: 1 } }),
];
const ALL = [...s7, question, reply, ...s8];

const sessions = () => screen.getAllByTestId("session");

describe("세션 묶기 (순수 함수)", () => {
  it("run_id 별로 처음 나온 자리에 묶고, 사용자 메시지·답장은 그대로 둔다", () => {
    const items = groupFeed(ALL);
    expect(items.map((it) => (it.type === "session" ? `#${it.runId}` : it.msg.content))).toEqual([
      "#7",
      "왜 샀어?",
      "실적 때문이에요",
      "#8",
    ]);
    const first = items[0];
    expect(first.type === "session" && first.msgs).toHaveLength(s7.length);
  });

  it("시작·시장 관점·결정·도구·메모를 나누고 상태를 정한다", () => {
    const v = summarizeSession(8, s8);
    expect(v.status).toBe("done");
    expect(v.trigger).toBe("hourly");
    expect(v.marketView).toBe("외국인 순매수 지속");
    expect(v.summary).toBe("다온건설 정리하려 했어요");
    expect(v.decisions).toHaveLength(2);
    expect(v.risk).toHaveLength(1);
    expect(v.tools).toHaveLength(7);
    expect(v.notes.map((n) => n.content)).toEqual(["뉴스부터 볼게요"]);
    expect(Object.fromEntries(v.sources)).toEqual({ 토스증권: 3, NAVER: 2, DART: 1, 모의계좌: 1 });
    // 서버 기록이 있으면 상태·소요 시간은 그쪽을 따른다
    const withRun = summarizeSession(8, s8, {
      status: "FAILED",
      started_at: "2026-11-02T08:40:00+09:00",
      finished_at: "2026-11-02T08:43:12+09:00",
    });
    expect(withRun.status).toBe("failed");
    expect(withRun.durationMs).toBe(192_000);
  });

  it("서버 기록이 RUNNING 이어도 지금 도는 세션이 아니면 '중단됨'", () => {
    const partial = s8.slice(0, 3);
    expect(summarizeSession(8, partial, { status: "RUNNING" }).status).toBe("running");
    expect(summarizeSession(8, partial, { status: "RUNNING" }, false).status).toBe("ended");
    expect(summarizeSession(8, partial, { status: "RUNNING" }, true).status).toBe("running");
    expect(summarizeSession(8, partial).status).toBe("ended");
  });

  it("도구 이름이 없는 예전 메시지는 라벨로 분류한다", () => {
    expect(toolCategory(msg(1, "analyst", "tool", "📰 뉴스 검색"))).toBe("news");
    expect(toolCategory(msg(1, "analyst", "tool", "🧾 재무제표 분석"))).toBe("research");
    expect(toolCategory(msg(1, "analyst", "tool", "🔧 무언가"))).toBe("other");
  });
});

describe("트레이딩 룸 세션 블록", () => {
  const feed = (extra: Partial<Parameters<typeof ChatFeed>[0]> = {}) =>
    renderWithProviders(
      <ChatFeed messages={ALL} typing={null} persona={persona} showInput={false} groupSessions {...extra} />,
    );

  it("세션마다 블록 하나: 머리줄에 번호·종류·상태·도구 수, 사용자 대화는 말풍선 그대로", () => {
    feed();
    const [old, latest] = sessions();
    expect(sessions()).toHaveLength(2);
    expect(old).toHaveTextContent("세션 #7");
    expect(old).toHaveTextContent("장전");
    expect(within(old).getByTestId("session-status")).toHaveTextContent("완료");
    expect(old).toHaveTextContent("도구 1회");
    expect(latest).toHaveTextContent("세션 #8");
    expect(latest).toHaveTextContent("정시");
    expect(latest).toHaveTextContent("도구 7회");
    // 사용자 질문과 매니저 답장은 세션 밖, 원래 말풍선으로
    expect(screen.getByText("왜 샀어?").closest("[data-testid=session]")).toBeNull();
    expect(screen.getByText("실적 때문이에요").closest(".bubble")).not.toBeNull();
    // 세션 시작 안내(구분선)는 머리줄로 대신한다
    expect(screen.queryByText(/출근했어요/)).not.toBeInTheDocument();
  });

  it("최신 세션은 펼치고(시장 판단·결정 카드), 예전 세션은 머리줄·요약·결정 한 줄로 접는다", () => {
    feed();
    const [old, latest] = sessions();
    const head = (el: HTMLElement) => within(el).getAllByRole("button")[0];
    expect(head(latest)).toHaveAttribute("aria-expanded", "true");
    expect(latest).toHaveTextContent("시장 판단");
    expect(latest).toHaveTextContent("외국인 순매수 지속");
    expect(within(latest).getAllByTestId("decision-card")).toHaveLength(2);
    expect(within(latest).getByText(/하루 한도/)).toBeInTheDocument();

    expect(head(old)).toHaveAttribute("aria-expanded", "false");
    expect(within(old).getByTestId("session-summary")).toHaveTextContent("가온전자 조금 샀어요");
    expect(within(old).queryByTestId("decision-card")).not.toBeInTheDocument();
    const compact = within(old).getByTestId("session-decisions");
    expect(compact).toHaveTextContent("매수가온전자12주 · 확신도 70%");
    expect(compact).toHaveTextContent("유지1종목 · 나래식품");

    fireEvent.click(head(old));
    expect(head(old)).toHaveAttribute("aria-expanded", "true");
    expect(within(old).getAllByTestId("decision-card")).toHaveLength(2);
    fireEvent.click(head(latest));
    expect(within(latest).queryByTestId("decision-card")).not.toBeInTheDocument();
  });

  it("확인한 정보는 접혀 있다가 펼치면 분류별 개수·출처·도구 칩을 보여준다", () => {
    feed();
    const latest = sessions()[1];
    expect(within(latest).queryByTestId("chat-tool")).not.toBeInTheDocument();
    const toggle = within(latest).getByRole("button", { name: /확인한 정보/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveTextContent("계좌 1");
    expect(toggle).toHaveTextContent("시세·차트 2");
    expect(toggle).toHaveTextContent("수급 1");
    expect(toggle).toHaveTextContent("재무·공시 1");
    expect(toggle).toHaveTextContent("뉴스 2");

    fireEvent.click(toggle);
    expect(within(latest).getAllByTestId("chat-tool")).toHaveLength(7);
    const news = within(latest).getByTestId("session-cat-news");
    expect(news).toHaveTextContent("NAVER 2");
    expect(news).toHaveTextContent("실패 1");
    expect(within(latest).getByTestId("session-cat-research")).toHaveTextContent("DART 1");

    // 매니저 메모도 따로 접혀 있다
    expect(within(latest).queryByText("뉴스부터 볼게요")).not.toBeInTheDocument();
    fireEvent.click(within(latest).getByRole("button", { name: /매니저 메모/ }));
    expect(within(latest).getByText("뉴스부터 볼게요")).toBeInTheDocument();
  });

  it("'리서치 과정 보기'를 끄면 확인한 정보 섹션을 숨긴다", () => {
    feed({ showTools: false });
    expect(within(sessions()[1]).queryByRole("button", { name: /확인한 정보/ })).not.toBeInTheDocument();
    expect(within(sessions()[1]).getAllByTestId("decision-card")).toHaveLength(2);
  });

  it("유지 결정이 많으면 이름만 한 줄로 모으고 눌러야 카드가 보인다", () => {
    const many = [
      decision(9, "BUY", "가"),
      ...["나", "다", "라", "마"].map((n) => decision(9, "HOLD", n)),
      msg(9, "analyst", "summary", "정리", {}),
    ];
    renderWithProviders(
      <ChatFeed messages={many} typing={null} persona={persona} showInput={false} groupSessions />,
    );
    expect(screen.getAllByTestId("decision-card")).toHaveLength(1);
    const fold = screen.getByRole("button", { name: /4종목/ });
    expect(fold).toHaveTextContent("나, 다, 라, 마");
    fireEvent.click(fold);
    expect(screen.getAllByTestId("decision-card")).toHaveLength(5);
  });

  it("실패한 세션은 짧은 실패 안내만, 오류 원문은 펼쳐 볼 수 있게", () => {
    const failed = [
      msg(72, "system", "text", "👆 수동 실행 — 🦉 알파 매니저가 출근했어요. (세션 #72)", {
        trigger: "manual",
      }),
      tool(72, "get_portfolio", "💼 포트폴리오 확인", "모의계좌"),
      msg(
        72,
        "system",
        "text",
        "⚠️ 세션 #72 중 문제가 생겼어요: LLMError: 모델이 submit_decisions 를 호출하지 않았습니다. 이번 세션 주문은 내지 않았어요.",
      ),
    ];
    renderWithProviders(
      <ChatFeed messages={failed} typing={null} persona={persona} showInput={false} groupSessions />,
    );
    const block = screen.getByTestId("session");
    expect(block).toHaveClass("failed");
    expect(within(block).getByTestId("session-status")).toHaveTextContent("실패");
    expect(within(block).getByTestId("session-fail")).toHaveTextContent("주문은 내지 않았어요");
    const details = block.querySelector("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("오류 내용 보기")).toBeInTheDocument();
    expect(details.querySelector("code")).toHaveTextContent(
      /^LLMError: 모델이 submit_decisions 를 호출하지 않았습니다$/,
    );
    // 원래의 긴 경고 구분선은 따로 그리지 않는다
    expect(screen.queryByTestId("chat-system")).not.toBeInTheDocument();
  });

  it("진행 중인 세션은 지금 하는 일·방금 확인한 도구 하나만 보여준다", () => {
    const running = s8.slice(0, 9); // 결정 전
    const progress: AgentProgress = {
      active: true,
      run_id: 8,
      phase: "tool",
      label: "📰 반도체 뉴스 검색",
      step: 3,
      max_steps: 8,
      tools: 7,
      typing: null,
      replying: false,
    };
    renderWithProviders(
      <ChatFeed
        messages={running}
        typing={null}
        progress={progress}
        persona={persona}
        showInput={false}
        groupSessions
      />,
    );
    const block = screen.getByTestId("session");
    expect(within(block).getByTestId("session-status")).toHaveTextContent("진행 중");
    const live = within(block).getByTestId("session-live");
    expect(live).toHaveTextContent("반도체 뉴스 검색");
    expect(live).toHaveTextContent("단계 3/8 · 도구 7회");
    expect(live).toHaveTextContent("💭 뉴스부터 볼게요");
    // 도구 줄을 전부 늘어놓지 않고 마지막 하나만
    expect(within(block).getAllByTestId("chat-tool")).toHaveLength(1);
    expect(within(live).getByTestId("chat-tool")).toHaveTextContent("반도체");
  });

  it("묶기를 켜지 않으면(대시보드) 예전처럼 한 줄씩 그린다", () => {
    renderWithProviders(<ChatFeed messages={ALL} typing={null} persona={persona} showInput={false} />);
    expect(screen.queryByTestId("session")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("chat-tool")).toHaveLength(8);
  });
});

describe("트레이딩 룸 세션 목록", () => {
  const RUNS: AgentRun[] = [
    {
      id: 72,
      trigger: "manual",
      mode: "llm-tools",
      model: "m",
      status: "FAILED",
      started_at: "2026-11-02T09:00:00+09:00",
      finished_at: "2026-11-02T09:00:45+09:00",
      summary: "",
      market_view: "",
      error: "LLMError: 모델이 submit_decisions 를 호출하지 않았습니다",
      usage: {},
    },
    {
      id: 71,
      trigger: "hourly",
      mode: "llm-tools",
      model: "m",
      status: "DONE",
      started_at: "2026-11-02T08:00:00+09:00",
      finished_at: "2026-11-02T08:03:12+09:00",
      summary: "오늘은 관망",
      market_view: "",
      error: null,
      usage: {},
    },
  ];

  it("상태를 한글로, 소요 시간과 요약을 짧게 보여준다", async () => {
    mockFetch({ "/api/agent/runs": RUNS, "/api/system": SYSTEM });
    renderWithProviders(<TradingRoom />);
    await waitFor(() => expect(screen.getAllByTestId("run-item")).toHaveLength(2));
    const [failed, done] = screen.getAllByTestId("run-item");
    expect(failed).toHaveTextContent("세션 #72");
    expect(failed).toHaveTextContent("수동");
    expect(failed).toHaveTextContent("실패");
    expect(failed).toHaveTextContent("45초");
    expect(failed.querySelector(".run-error")).toHaveAttribute("title", RUNS[0].error);
    expect(done).toHaveTextContent("정시");
    expect(done).toHaveTextContent("완료");
    expect(done).toHaveTextContent("3분 12초");
    expect(done).toHaveTextContent("오늘은 관망");
    expect(done).not.toHaveTextContent("DONE");
  });
});
