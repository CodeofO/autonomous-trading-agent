import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "../components/Layout";
import { swipeTarget, topbarCompact } from "../components/nav";
import { blocksSwipe, swipeDirection } from "../ui/swipe";
import { clearToasts, toast } from "../ui/toast";
import { mockFetch, renderWithProviders } from "./utils";

function setMobile(mobile: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: mobile && query.includes("max-width"), media: query })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearToasts();
});

describe("넘기기 판정", () => {
  it("빠르고 충분히 옆으로 움직였을 때만 넘기기로 본다", () => {
    expect(swipeDirection(-90, 10, 200)).toBe("left");
    expect(swipeDirection(90, -20, 300)).toBe("right");
    expect(swipeDirection(40, 0, 100)).toBeNull(); // 너무 짧다
    expect(swipeDirection(90, 70, 200)).toBeNull(); // 비스듬한 스크롤
    expect(swipeDirection(120, 0, 1200)).toBeNull(); // 천천히 끌기
  });

  it("탭 화면은 옆 탭으로, 끝에서는 멈추고, 그 밖의 화면은 오른쪽으로 넘기면 뒤로", () => {
    expect(swipeTarget("/", "left", false)).toBe("/room");
    expect(swipeTarget("/room", "right", false)).toBe("/");
    expect(swipeTarget("/portfolio", "left", true)).toBe("/news");
    expect(swipeTarget("/news", "left", true)).toBeNull(); // 마지막 탭 (더보기는 넘겨서 열지 않는다)
    expect(swipeTarget("/", "right", true)).toBeNull();
    expect(swipeTarget("/stock/005930", "right", true)).toBe(-1);
    expect(swipeTarget("/settings/llm", "right", false)).toBeNull(); // 앱 안에 뒤로 갈 곳이 없으면 그대로
    expect(swipeTarget("/agent", "left", true)).toBeNull();
  });

  it("상단 바는 내리면 접고, 조금이라도 올리면 펴고, 맨 위 근처에서는 항상 편다", () => {
    expect(topbarCompact(false, 100, 140)).toBe(true);
    expect(topbarCompact(true, 300, 280)).toBe(false);
    expect(topbarCompact(true, 300, 302)).toBe(true); // 작은 흔들림은 그대로
    expect(topbarCompact(true, 80, 40)).toBe(false);
  });

  it("가로 스크롤 상자·입력칸·차트 위에서는 넘기지 않는다", () => {
    document.body.innerHTML = `
      <div id="root">
        <div id="strip" style="overflow-x: auto"><span id="in-strip">지표</span></div>
        <input id="field" />
        <div class="recharts-wrapper"><i id="chart"></i></div>
        <p id="plain">글</p>
      </div>`;
    const root = document.getElementById("root")!;
    const strip = document.getElementById("strip")!;
    Object.defineProperty(strip, "scrollWidth", { value: 600 });
    Object.defineProperty(strip, "clientWidth", { value: 300 });
    expect(blocksSwipe(document.getElementById("in-strip"), root)).toBe(true);
    expect(blocksSwipe(document.getElementById("field"), root)).toBe(true);
    expect(blocksSwipe(document.getElementById("chart"), root)).toBe(true);
    expect(blocksSwipe(document.getElementById("plain"), root)).toBe(false);
    document.body.innerHTML = "";
  });
});

function Where() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function swipe(el: Element, dx: number, dy = 0) {
  fireEvent.touchStart(el, { touches: [{ clientX: 200, clientY: 300 }] });
  fireEvent.touchMove(el, { touches: [{ clientX: 200 + dx / 2, clientY: 300 + dy / 2 }] });
  fireEvent.touchEnd(el, { changedTouches: [{ clientX: 200 + dx, clientY: 300 + dy }] });
}

describe("휴대폰 넘기기로 탭 이동", () => {
  function renderAt(route: string) {
    mockFetch({});
    return renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Where />} />
          <Route path="room" element={<Where />} />
          <Route path="portfolio" element={<Where />} />
          <Route path="news" element={<Where />} />
        </Route>
      </Routes>,
      { route },
    );
  }

  it("홈에서 왼쪽으로 넘기면 채팅, 다시 오른쪽으로 넘기면 홈", async () => {
    setMobile(true);
    renderAt("/");
    const scroller = screen.getByTestId("page-scroll");
    swipe(scroller, -120);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/room"));
    swipe(scroller, 120);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
  });

  it("세로 스크롤·짧은 이동은 그대로, 넓은 화면에서는 넘기기를 하지 않는다", async () => {
    setMobile(true);
    const { unmount } = renderAt("/room");
    const scroller = screen.getByTestId("page-scroll");
    swipe(scroller, -40); // 짧다
    swipe(scroller, -90, 120); // 세로 스크롤
    await act(async () => {});
    expect(screen.getByTestId("where")).toHaveTextContent("/room");
    unmount();

    setMobile(false);
    renderAt("/room");
    swipe(screen.getByTestId("page-scroll"), -120);
    await act(async () => {});
    expect(screen.getByTestId("where")).toHaveTextContent("/room");
  });
});

describe("토스트 밀어서 닫기", () => {
  it("옆으로 충분히 밀면 닫히고, 조금 밀면 제자리로", async () => {
    renderWithProviders(<div />); // 앱처럼 토스트 자리(<Toaster />)를 함께 그린다
    act(() => toast("저장했어요 ✓"));
    const t = await screen.findByTestId("toast");
    fireEvent.touchStart(t, { touches: [{ clientX: 100, clientY: 0 }] });
    fireEvent.touchMove(t, { touches: [{ clientX: 120, clientY: 0 }] });
    fireEvent.touchEnd(t, { changedTouches: [{ clientX: 120, clientY: 0 }] });
    expect(screen.getByTestId("toast")).toBeInTheDocument();
    expect(t.style.transform).toBe("");
    fireEvent.touchStart(t, { touches: [{ clientX: 100, clientY: 0 }] });
    fireEvent.touchEnd(t, { changedTouches: [{ clientX: 190, clientY: 0 }] });
    await waitFor(() => expect(screen.queryByTestId("toast")).not.toBeInTheDocument());
  });
});
