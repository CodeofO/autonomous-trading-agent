import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { MarketInfo, MarketView } from "../api/types";
import { MarketProvider } from "../components/MarketProvider";
import { Toaster } from "../components/Toaster";
import { MARKETS_KEY } from "../market";

/** GET /api/markets 한 줄 — 국장 (오늘 09:00–15:30 KST) */
export const KR_MARKET: MarketInfo = {
  code: "KR",
  name: "국장",
  currency: "KRW",
  timezone: "Asia/Seoul",
  enabled: true,
  paper_only: false,
  trading_mode: "paper",
  llm_profile: "free",
  llm_model: "test-model",
  agent_running: false,
  engine_status: "RUNNING",
  open_now: true,
  session: {
    day: "2026-11-04",
    open: "2026-11-04T09:00:00+09:00",
    close: "2026-11-04T15:30:00+09:00",
    order_cutoff: "2026-11-04T15:20:00+09:00",
  },
  next_session_open: "2026-11-05T09:00:00+09:00",
};

/** 미장 (밤 23:30 – 다음날 06:00 KST, 늘 모의) */
export const US_MARKET: MarketInfo = {
  ...KR_MARKET,
  code: "US",
  name: "미장",
  currency: "USD",
  timezone: "America/New_York",
  paper_only: true,
  open_now: false,
  session: {
    day: "2026-11-04",
    open: "2026-11-04T23:30:00+09:00",
    close: "2026-11-05T06:00:00+09:00",
    order_cutoff: "2026-11-05T05:50:00+09:00",
  },
  next_session_open: "2026-11-04T23:30:00+09:00",
};

export const BOTH_MARKETS = [KR_MARKET, US_MARKET];

interface Options {
  /** 처음 보기 (전체·국장·미장). 없으면 주소(?m=)·저장소·기본값 순 */
  market?: MarketView;
  /** /api/markets 응답을 미리 넣어 둔다. 기본은 국장만 — 시장 전환이 없는 예전 화면 그대로 (요청도 없다) */
  markets?: MarketInfo[];
  /** 처음 주소 (기본 "/") */
  route?: string;
}

export function renderWithProviders(ui: ReactNode, opts: Options = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  qc.setQueryData(MARKETS_KEY, opts.markets ?? [KR_MARKET]);
  // 앱처럼 토스트(버튼을 누른 결과)도 함께 그린다 — App 맨 위의 <Toaster /> 자리
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[opts.route ?? "/"]}>
        <MarketProvider initial={opts.market}>{children}</MarketProvider>
      </MemoryRouter>
      <Toaster />
    </QueryClientProvider>
  );
  return { ...render(ui, { wrapper }), qc };
}

// 핸들러는 Promise 를 돌려줘도 된다 (응답을 붙잡아 두고 화면의 중간 상태를 볼 때)
type Handler = (url: string, init?: RequestInit) => unknown;

export function mockFetch(routes: Record<string, Handler | unknown>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
    const v = routes[key];
    const body = typeof v === "function" ? await (v as Handler)(url, init) : v;
    // 오류 응답 등은 핸들러가 Response 를 그대로 돌려준다 (예: apiError(409, "..."))
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** FastAPI 오류 응답 모양 ({ detail }) */
export const apiError = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json" } });

export const SYSTEM = {
  trading_mode: "paper",
  data_source: "toss",
  toss_ok: true,
  toss_issue: null,
  agent_interval_minutes: 60,
  now: "2026-11-02T10:00:00+09:00",
  llm: { configured: false, model: null, profile: "free", tool_mode: "auto" },
  research: { financials: "MOCK", news: "MOCK" },
  engine_status: "RUNNING",
  agent_running: false,
  order_window: "지금 주문하면 2026-11-02 종가로 체결",
  latest_price_day: "2026-10-30",
  next_runs: [],
  persona: { name: "알파", emoji: "🦉" },
  disclaimer: "모의투자(PAPER) 전용 교육·실험 시스템입니다. 투자 권유가 아닙니다.",
};
