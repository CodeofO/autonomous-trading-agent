import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { KillSwitch } from "../components/KillSwitch";
import { Layout } from "../components/Layout";
import { Login } from "../pages/Login";
import { RiskSettings } from "../pages/settings/RiskSection";
import { validateRisk } from "../utils/risk";
import { fmtPct, fmtWon, signClass } from "../utils/format";
import { mockFetch, renderWithProviders, SYSTEM } from "./utils";

afterEach(() => vi.unstubAllGlobals());

describe("킬 스위치", () => {
  it("확인 모달을 거쳐야만 정지 API 를 호출한다", async () => {
    const fetchMock = mockFetch({ "/api/engine/kill": { status: "KILLED", canceled: 2 } });
    renderWithProviders(<KillSwitch status="RUNNING" />);
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    expect(screen.getByRole("dialog", { name: /킬 스위치를 작동할까요/ })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "즉시 전체 정지" }));
    // 정지 뒤에는 모든 화면을 다시 받는다(시장 목록 포함) — 정지 요청만 골라 본다
    const kills = () => fetchMock.mock.calls.filter(([u]) => u === "/api/engine/kill");
    await waitFor(() => expect(kills()).toHaveLength(1));
    const [, init] = kills()[0];
    expect((init as RequestInit).method).toBe("POST");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("취소하면 아무것도 호출하지 않는다", () => {
    const fetchMock = mockFetch({});
    renderWithProviders(<KillSwitch status="RUNNING" />);
    fireEvent.click(screen.getByRole("button", { name: "킬 스위치" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("정지 상태에서는 재개 버튼을 보여준다", () => {
    mockFetch({});
    renderWithProviders(<KillSwitch status="KILLED" />);
    expect(screen.getByRole("button", { name: /재개/ })).toBeInTheDocument();
  });
});

describe("레이아웃", () => {
  it("모의투자 PAPER 배지와 엔진 상태를 항상 보여준다", async () => {
    mockFetch({ "/api/system": SYSTEM });
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>본문</div>} />
        </Route>
      </Routes>,
    );
    expect(screen.getByRole("status", { name: "모의투자 모드" })).toHaveTextContent("PAPER");
    await waitFor(() => expect(screen.getByTestId("engine-status")).toHaveTextContent("실행 중"));
    expect(screen.getByText(/투자 권유가 아닙니다/)).toBeInTheDocument();
    expect(screen.getByTestId("kst-clock")).toHaveTextContent("KST");
    // 토스 시세가 정상이면 칩을 두지 않는다 (문제가 있을 때만 경고 칩·배너)
    expect(screen.queryByText("토스 시세 연결 안 됨")).not.toBeInTheDocument();
    // 설정은 오른쪽 위 ⚙️
    expect(screen.getByRole("link", { name: "설정" })).toHaveAttribute("href", "/settings");
  });

  it("실거래 모드면 빨간 LIVE 배지로 바뀐다", async () => {
    mockFetch({ "/api/system": { ...SYSTEM, trading_mode: "live" } });
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>본문</div>} />
        </Route>
      </Routes>,
    );
    await waitFor(() =>
      expect(screen.getByRole("status", { name: "실거래 모드" })).toHaveTextContent("LIVE"),
    );
    expect(screen.queryByRole("status", { name: "모의투자 모드" })).not.toBeInTheDocument();
  });

  it("넓은 화면 메뉴는 두 묶음이고(판단 일정 바로가기 포함), 설정은 사이드바에 두지 않는다", async () => {
    mockFetch({});
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>본문</div>} />
        </Route>
      </Routes>,
    );
    const nav = screen.getByRole("navigation", { name: "메뉴" });
    const group = (name: string) =>
      within(within(nav).getByRole("group", { name }))
        .getAllByRole("link")
        .map((a) => a.getAttribute("href"));
    expect(group("매일 보기")).toEqual(["/", "/room", "/portfolio", "/news"]);
    expect(group("매니저·분석")).toEqual(["/agent", "/lab", "/settings/schedule", "/backtest", "/logs", "/costs"]);
    expect(within(nav).getByRole("link", { name: "홈" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link", { name: "설정" })).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: /연결/ })).toBeInTheDocument(); // 모바일 상단 연결 점
  });

  it("토스 연결 문제를 배너로 알린다", async () => {
    mockFetch({ "/api/system": { ...SYSTEM, toss_ok: false, toss_issue: "허용 IP 미등록" } });
    renderWithProviders(
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<div>본문</div>} />
        </Route>
      </Routes>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("허용 IP 미등록");
  });
});

describe("리스크 설정 검증", () => {
  const ok = {
    max_position_weight: 20,
    max_order_amount: 1_000_000,
    daily_loss_limit: -3,
    stop_loss: -5,
    take_profit: 10,
    min_order_interval_minutes: 60,
    max_positions: 10,
    min_cash_ratio: 5,
  };
  it("경계값", () => {
    expect(validateRisk(ok)).toBeNull();
    expect(validateRisk({ ...ok, stop_loss: 5 })).toMatch(/손절/);
    expect(validateRisk({ ...ok, stop_loss: 0 })).toMatch(/손절/);
    expect(validateRisk({ ...ok, daily_loss_limit: 0 })).toMatch(/일일/);
    expect(validateRisk({ ...ok, max_position_weight: 0 })).toMatch(/비중/);
    expect(validateRisk({ ...ok, max_position_weight: 100 })).toBeNull();
    expect(validateRisk({ ...ok, max_positions: 1.5 })).toMatch(/정수/);
  });

  it("잘못된 값은 저장 요청 없이 오류를 보여준다", async () => {
    const config = {
      max_position_weight: 0.2,
      max_order_amount: 1_000_000,
      daily_loss_limit: -0.03,
      stop_loss: -0.05,
      take_profit: 0.1,
      min_order_interval_minutes: 60,
      max_positions: 10,
      min_cash_ratio: 0.05,
      allow_new_buys: true,
    };
    const fetchMock = mockFetch({
      "/api/risk": (_u: string, init?: RequestInit) => (init?.method === "PUT" ? { config } : { config }),
    });
    renderWithProviders(<RiskSettings />);
    const input = await screen.findByLabelText("종목별 손절");
    await waitFor(() => expect(input).toHaveValue(-5));
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(await screen.findByText(/손절은 음수/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([, i]) => (i as RequestInit | undefined)?.method === "PUT"),
    ).toHaveLength(0);
    fireEvent.change(input, { target: { value: "-7" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT");
      expect(put).toBeTruthy();
      expect(JSON.parse((put![1] as RequestInit).body as string).stop_loss).toBeCloseTo(-0.07);
    });
  });
});

describe("로그인", () => {
  it("첫 실행이면 계정 만들기 화면(비밀번호 확인 포함)", async () => {
    mockFetch({ "/api/auth/status": { setup_required: true } });
    renderWithProviders(<Login />);
    expect(await screen.findByText("계정 만들고 시작하기")).toBeInTheDocument();
    expect(screen.getByLabelText("비밀번호 확인")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText("비밀번호 확인"), { target: { value: "short" } });
    fireEvent.click(screen.getByText("계정 만들고 시작하기"));
    expect(await screen.findByText(/8자 이상/)).toBeInTheDocument();
  });
});

describe("포맷", () => {
  it("원화·퍼센트·부호 색", () => {
    expect(fmtWon(1234567)).toBe("1,234,567원");
    expect(fmtPct(1.234)).toBe("+1.23%");
    expect(fmtPct(-2)).toBe("-2.00%");
    expect(signClass(1)).toBe("up");
    expect(signClass(-1)).toBe("down");
    expect(fmtWon(null)).toBe("—");
  });
});
