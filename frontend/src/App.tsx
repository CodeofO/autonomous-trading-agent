import { Suspense, useEffect, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { EventsProvider } from "./events";
import { MarketProvider } from "./components/MarketProvider";
import { useMarket } from "./market";
import { Layout } from "./components/Layout";
import { ManagerTabs } from "./components/ManagerTabs";
import { Toaster } from "./components/Toaster";
import { prefetchOverviewWhenIdle } from "./utils/overview";
import {
  AgentRoute,
  BacktestPage,
  CostsPage,
  DashboardPage,
  LabPage,
  LoginPage,
  LogsPage,
  MemoRoute,
  NewsRoomRoute,
  OverviewPage,
  PortfolioPage,
  prefetchPagesWhenIdle,
  SettingsPage,
  StockPage,
  TradingRoomPage,
} from "./routes";

/** 첫 화면: '전체'면 두 시장을 한눈에, 시장을 고르면 그 시장 대시보드 */
function Home() {
  const { view } = useMarket();
  return view === "ALL" ? <OverviewPage /> : <DashboardPage />;
}

function Routed() {
  const { token, authEnabled, ready } = useAuth();
  if (!ready) return null;
  if (authEnabled && !token)
    return (
      <Suspense fallback={null}>
        <LoginPage />
      </Suspense>
    );
  return (
    <MarketProvider key={token ?? "local"}>
      <EventsProvider>
        <AppRoutes />
      </EventsProvider>
    </MarketProvider>
  );
}

function AppRoutes() {
  // 첫 화면이 뜨고 한가해지면 나머지 화면 조각을 받아 둔다 (메뉴·시장 전환이 기다림 없이 열린다)
  useEffect(() => prefetchPagesWhenIdle(), []);
  // '전체' 요약 데이터도 한가할 때 받아 둔다 (첫 시장 전환이 기다림 없이 뜨게)
  const qc = useQueryClient();
  const { available } = useMarket();
  const both = available.length > 1;
  useEffect(() => prefetchOverviewWhenIdle(qc, both ? ["KR", "US"] : ["KR"]), [qc, both]);
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="room" element={<TradingRoomPage />} />
        <Route path="news" element={<NewsRoomRoute />} />
        {/* 메뉴의 '매니저' 하나 — 구조·기억 / 메모 카드를 위 탭으로 오간다 */}
        <Route
          path="agent"
          element={
            <>
              <ManagerTabs />
              <AgentRoute />
            </>
          }
        />
        <Route
          path="memo"
          element={
            <>
              <ManagerTabs />
              <MemoRoute />
            </>
          }
        />
        <Route path="portfolio" element={<PortfolioPage />} />
        <Route path="stock/:symbol" element={<StockPage />} />
        <Route path="backtest" element={<BacktestPage />} />
        <Route path="lab" element={<LabPage />} />
        {/* /settings 는 주제 목록(넓은 화면은 목록 + LLM 모델), /settings/llm·risk… 는 주제 하나 */}
        <Route path="settings/:section?" element={<SettingsPage />} />
        <Route path="costs" element={<CostsPage />} />
        <Route path="logs" element={<LogsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export function App({ client }: { client?: QueryClient }) {
  const [qc] = useState(
    () => client ?? new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 2000 } } }),
  );
  return (
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <BrowserRouter>
          <Routed />
        </BrowserRouter>
      </AuthProvider>
      {/* 버튼을 누른 결과(저장·전환·정지·재개…)를 잠깐 띄우는 토스트 — 앱에 하나 */}
      <Toaster />
    </QueryClientProvider>
  );
}
