import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../api/types";
import { ChatMessage } from "../components/chat/ChatMessage";
import { renderWithProviders } from "./utils";

const persona = { name: "알파", emoji: "🦉" };
const tool = (payload: Record<string, unknown>, content = "📰 뉴스 검색: “삼성전자”"): ChatMsg => ({
  id: 1,
  ts: "2026-11-02T08:40:00+09:00",
  run_id: 1,
  role: "analyst",
  kind: "tool",
  content,
  payload,
});

const news = tool({
  tool: "search_news",
  args: { query: "삼성전자", limit: 5, _trace: "x" },
  source: "NAVER",
  ms: 1420,
  step: 3,
  ok: true,
  line: "뉴스 5건 · 「삼성전자 실적…」",
  preview: {
    titles: ["삼성전자 3분기 실적 시장 예상치 상회", "반도체 업황 회복 기대감 커져"],
    count: 5,
    returns_pct: { "5d": 1.25, "20d": 3.2 },
  },
});

describe("도구 호출 칩", () => {
  it("출처·결과 요약·소요 시간을 한 줄로 보여준다", () => {
    renderWithProviders(<ChatMessage msg={news} persona={persona} />);
    const row = screen.getByTestId("chat-tool");
    expect(row).not.toHaveClass("fail");
    expect(within(row).getByText("NAVER")).toHaveClass("tool-src", "naver");
    expect(within(row).getByText("뉴스 5건 · 「삼성전자 실적…」")).toBeInTheDocument();
    expect(within(row).getByText("1.4s")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("펼치면 요청 인자를 한글 이름으로, 결과를 읽기 쉬운 값으로 보여준다", () => {
    renderWithProviders(<ChatMessage msg={news} persona={persona} />);
    expect(screen.queryByText("검색어")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /뉴스 검색/ }));
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("요청")).toBeInTheDocument();
    expect(screen.getByText("검색어")).toBeInTheDocument();
    expect(screen.getByText("삼성전자")).toBeInTheDocument();
    expect(screen.getByText("개수")).toBeInTheDocument();
    expect(screen.queryByText("_trace")).not.toBeInTheDocument();
    expect(screen.getByText("결과")).toBeInTheDocument();
    expect(screen.getByText("반도체 업황 회복 기대감 커져")).toBeInTheDocument();
    expect(screen.getByText("5d: 1.25 · 20d: 3.2")).toBeInTheDocument();
    expect(screen.getByText("search_news")).toBeInTheDocument();
    expect(screen.getByText(/3단계/)).toBeInTheDocument();
  });

  it("실패한 호출은 경고 스타일로 표시한다", () => {
    const failed = tool(
      {
        tool: "get_price_history",
        args: { symbol: "005930", days: 60 },
        source: "토스증권",
        ms: 120,
        ok: false,
        line: "실패: HTTP 401",
        preview: { error: "HTTP 401" },
      },
      "📈 삼성전자 차트·지표 확인",
    );
    renderWithProviders(<ChatMessage msg={failed} persona={persona} />);
    const row = screen.getByTestId("chat-tool");
    expect(row).toHaveClass("fail");
    expect(within(row).getByText("실패: HTTP 401")).toHaveClass("tool-line");
    expect(within(row).getByText("120ms")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("기간(일)")).toBeInTheDocument();
    expect(screen.getByText("오류")).toBeInTheDocument();
  });

  it("새 필드가 없는 예전 메시지와 규칙 모드 메시지도 그린다", () => {
    const old = tool({ tool: "get_financials", args: { symbol: "MOCK01" }, preview: {} }, "🧾 재무제표 분석");
    const rule = tool(
      { tool: "rule_score", symbol: "MOCK01", preview: { score: 0.4, evidence: ["20일 수익률 +3%"] } },
      "🧮 규칙 점수 계산",
    );
    const { unmount } = renderWithProviders(<ChatMessage msg={old} persona={persona} />);
    expect(screen.getByTestId("chat-tool")).toHaveTextContent("재무제표 분석");
    expect(screen.queryByText(/ms$/)).not.toBeInTheDocument();
    unmount();

    renderWithProviders(<ChatMessage msg={rule} persona={persona} />);
    fireEvent.click(screen.getByRole("button", { name: /규칙 점수/ }));
    expect(screen.getByText("종목")).toBeInTheDocument();
    expect(screen.getByText("0.4")).toBeInTheDocument();
    expect(screen.getByText("20일 수익률 +3%")).toBeInTheDocument();
  });

  it("세부 내용이 없으면 펼칠 수 없다", () => {
    renderWithProviders(<ChatMessage msg={tool({ tool: "x", preview: {} }, "🔧 기타")} persona={persona} />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
