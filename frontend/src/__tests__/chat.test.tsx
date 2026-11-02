import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import { ChatMessage } from "../components/chat/ChatMessage";
import { renderWithProviders } from "./utils";

const persona = { name: "알파", emoji: "🦉" };
const base = { ts: "2026-11-02T08:40:00+09:00", run_id: 1, payload: {} };

const decision: ChatMsg = {
  ...base,
  id: 3,
  role: "analyst",
  kind: "decision",
  content: "🟢 매수 가온전자",
  payload: {
    symbol: "MOCK01",
    name: "가온전자",
    action: "BUY",
    quantity: 12,
    target_weight: 0.1,
    confidence: 0.72,
    rationale: "실적이 좋아졌어요",
    evidence: ["PER 8.2배", "뉴스: 대규모 수주"],
    price: 84600,
  },
};

describe("트레이딩 룸 메시지", () => {
  it("결정 카드에 행동·확신도·근거를 보여준다", () => {
    renderWithProviders(<ChatMessage msg={decision} persona={persona} />);
    const card = screen.getByTestId("decision-card");
    expect(card).toHaveClass("buy");
    expect(card).toHaveTextContent("매수");
    expect(card).toHaveTextContent("가온전자");
    expect(card).toHaveTextContent("12주");
    expect(card).toHaveTextContent("확신도 72%");
    expect(screen.getByText("PER 8.2배")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /가온전자/ })).toHaveAttribute("href", "/stock/MOCK01");
  });

  it("도구 호출 칩은 눌러야 결과 미리보기가 펼쳐진다", () => {
    const tool: ChatMsg = {
      ...base,
      id: 2,
      role: "analyst",
      kind: "tool",
      content: "🧾 가온전자 재무제표 분석",
      payload: { tool: "get_financials", preview: { per: 8.2 } },
    };
    renderWithProviders(<ChatMessage msg={tool} persona={persona} />);
    expect(screen.queryByText("PER")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /재무제표 분석/ }));
    expect(screen.getByText("PER")).toBeInTheDocument();
    expect(screen.getByText("8.2")).toBeInTheDocument();
  });

  it("리스크봇 차단 메시지와 시스템 구분선을 역할별로 그린다", () => {
    const block: ChatMsg = { ...base, id: 4, role: "risk", kind: "block", content: "🛑 잠깐! 막을게요" };
    const sys: ChatMsg = { ...base, id: 5, role: "system", kind: "text", content: "☀️ 장 시작 전 점검" };
    renderWithProviders(
      <>
        <ChatMessage msg={block} persona={persona} />
        <ChatMessage msg={sys} persona={persona} />
      </>,
    );
    expect(screen.getByText("리스크봇")).toBeInTheDocument();
    expect(screen.getByTestId("chat-block")).toHaveClass("risk");
    expect(screen.getByTestId("chat-system")).toHaveTextContent("장 시작 전 점검");
  });

  it("입력 중 표시와 '리서치 과정 숨기기' 필터", () => {
    const tool: ChatMsg = {
      ...base,
      id: 2,
      role: "analyst",
      kind: "tool",
      content: "📰 뉴스 검색",
      payload: {},
    };
    const text: ChatMsg = { ...base, id: 6, role: "analyst", kind: "text", content: "뉴스부터 볼게요" };
    const { rerender } = renderWithProviders(
      <ChatFeed
        messages={[tool, text]}
        typing={{ role: "analyst", label: "📈 차트 확인" }}
        persona={persona}
        showInput={false}
      />,
    );
    expect(screen.getByTestId("typing")).toHaveTextContent("📈 차트 확인");
    expect(screen.getByText("📰 뉴스 검색")).toBeInTheDocument();
    rerender(
      <ChatFeed
        messages={[tool, text]}
        typing={null}
        persona={persona}
        showInput={false}
        showTools={false}
      />,
    );
    expect(screen.queryByText("📰 뉴스 검색")).not.toBeInTheDocument();
    expect(screen.getByText("뉴스부터 볼게요")).toBeInTheDocument();
  });

  it("빈 채팅방 안내", () => {
    renderWithProviders(<ChatFeed messages={[]} typing={null} persona={persona} showInput={false} />);
    expect(screen.getByText(/아직 조용하네요/)).toBeInTheDocument();
  });
});
