import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { Layout } from "../components/Layout";
import { Loading } from "../components/Loading";
import { tabIndex } from "../components/nav";
import { useMobileViewport } from "../mobile";
import { mockFetch, renderWithProviders } from "./utils";

// jsdom 에는 matchMedia 가 없다 — 휴대폰 폭인지 여부만 흉내 낸다
function setMobile(mobile: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: mobile && query.includes("max-width"), media: query })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.classList.remove("kb-open", "vt-nav");
});

describe("모바일 탭 위치", () => {
  it("경로를 하단 탭 순서로 바꾸고, 종목 상세는 '자산', 탭바에 없는 화면은 '더보기'로 본다", () => {
    expect(tabIndex("/")).toBe(0);
    expect(tabIndex("/room")).toBe(1);
    expect(tabIndex("/portfolio")).toBe(2);
    expect(tabIndex("/stock/005930")).toBe(2);
    expect(tabIndex("/news")).toBe(3);
    for (const p of ["/agent", "/memo", "/backtest", "/logs", "/costs", "/settings/llm"])
      expect(tabIndex(p)).toBe(4);
  });
});

function Field() {
  useMobileViewport();
  return (
    <>
      <input aria-label="글자" />
      <input type="checkbox" aria-label="체크" />
    </>
  );
}

describe("모바일 키보드", () => {
  it("휴대폰에서 글자 입력창에 들어가면 탭바를 숨기는 표시(kb-open)를 단다", () => {
    setMobile(true);
    render(<Field />);
    const root = document.documentElement;
    fireEvent.focusIn(screen.getByLabelText("체크"));
    expect(root).not.toHaveClass("kb-open");
    fireEvent.focusIn(screen.getByLabelText("글자"));
    expect(root).toHaveClass("kb-open");
    fireEvent.focusOut(screen.getByLabelText("글자"));
    expect(root).not.toHaveClass("kb-open");
  });

  it("넓은 화면에서는 아무 표시도 하지 않는다", () => {
    setMobile(false);
    render(<Field />);
    fireEvent.focusIn(screen.getByLabelText("글자"));
    expect(document.documentElement).not.toHaveClass("kb-open");
  });
});

function renderLayout() {
  return renderWithProviders(
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<div>홈 본문</div>} />
        <Route path="room" element={<div>룸 본문</div>} />
        <Route path="agent" element={<div>매니저 본문</div>} />
      </Route>
    </Routes>,
  );
}

describe("모바일 탭바", () => {
  it("휴대폰은 4칸 + 더보기, 선택된 탭 위치를 표시(--tab-i)로 넘긴다", () => {
    setMobile(true);
    mockFetch({});
    renderLayout();
    const nav = screen.getByRole("navigation", { name: "하단 탭" });
    expect(nav.style.getPropertyValue("--tab-i")).toBe("0");
    expect(nav.querySelector(".tab-ind")).not.toBeNull();
    expect(
      within(nav)
        .getAllByRole("link")
        .map((a) => a.getAttribute("href")),
    ).toEqual(["/", "/room", "/portfolio", "/news"]);
    expect(
      within(nav).getByRole("link", { name: "트레이딩 룸" }).querySelector(".nav-short"),
    ).toHaveTextContent("채팅");
    // 넓은 화면 메뉴는 그리지 않는다 (같은 링크가 두 번 읽히지 않게)
    expect(screen.queryByRole("navigation", { name: "메뉴" })).not.toBeInTheDocument();
  });

  it("더보기를 누르면 매니저·전략 실험실·판단 일정·백테스트·로그·LLM 요금·설정이 시트로 열리고, 고르면 닫히며 이동한다", async () => {
    setMobile(true);
    mockFetch({});
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: "더보기" }));
    const sheet = screen.getByRole("dialog", { name: "더보기" });
    expect(
      within(sheet)
        .getAllByRole("link")
        .map((a) => a.getAttribute("href")),
    ).toEqual(["/agent", "/lab", "/settings/schedule", "/backtest", "/logs", "/costs", "/settings"]);
    fireEvent.click(within(sheet).getByRole("link", { name: /^매니저/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "더보기" })).not.toBeInTheDocument());
    expect(screen.getByText("매니저 본문")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "더보기" })).toHaveAttribute("aria-current", "page");
  });

  it("휴대폰에서 지금 보고 있는 탭을 다시 누르면 이동 대신 맨 위로 올린다", () => {
    setMobile(true);
    mockFetch({});
    renderLayout();
    const scroller = screen.getByTestId("page-scroll");
    const scrollTo = vi.fn();
    scroller.scrollTo = scrollTo;
    fireEvent.click(screen.getByRole("link", { name: "홈" }));
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }));
    expect(screen.getByText("홈 본문")).toBeInTheDocument();
  });

  it("넓은 화면에서는 탭을 눌러도 평소처럼 이동만 한다", () => {
    setMobile(false);
    mockFetch({});
    renderLayout();
    const scroller = screen.getByTestId("page-scroll");
    const scrollTo = vi.fn();
    scroller.scrollTo = scrollTo;
    fireEvent.click(screen.getByRole("link", { name: "트레이딩 룸" }));
    expect(screen.getByText("룸 본문")).toBeInTheDocument();
    expect(scrollTo).not.toHaveBeenCalled();
    expect(document.documentElement).not.toHaveClass("vt-nav");
  });
});

describe("불러오는 중", () => {
  it("글자는 그대로 두고(넓은 화면) 휴대폰용 자리 표시 카드를 함께 그린다", () => {
    const { container } = render(<Loading cards={2} />);
    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    expect(container.querySelectorAll(".skeleton i")).toHaveLength(2);
    expect(container.firstChild).toHaveAttribute("aria-busy", "true");
  });
});
