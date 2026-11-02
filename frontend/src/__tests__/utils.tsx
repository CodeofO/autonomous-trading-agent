import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export function renderWithProviders(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
  return render(ui, { wrapper });
}

type Handler = (url: string, init?: RequestInit) => unknown;

export function mockFetch(routes: Record<string, Handler | unknown>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response(JSON.stringify({ detail: "not mocked" }), { status: 404 });
    const v = routes[key];
    const body = typeof v === "function" ? (v as Handler)(url, init) : v;
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const SYSTEM = {
  trading_mode: "paper",
  data_source: "mock",
  clock_mode: "simulated",
  now: "2026-11-02T10:00:00+09:00",
  sim_speed: 600,
  sim_paused: false,
  llm: { configured: false, model: null, host: null, tool_mode: "auto" },
  research: { financials: "MOCK", news: "MOCK" },
  engine_status: "RUNNING",
  agent_running: false,
  order_window: "지금 주문하면 2026-11-02 종가로 체결",
  latest_price_day: "2026-10-30",
  next_runs: [],
  persona: { name: "알파", emoji: "🦉" },
  disclaimer: "모의투자(PAPER) 전용 교육·실험 시스템입니다. 투자 권유가 아닙니다.",
};
