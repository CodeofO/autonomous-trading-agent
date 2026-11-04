import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { EventsProvider } from "./events";
import { Layout } from "./components/Layout";
import { AgentPage } from "./pages/Agent";
import { Backtest } from "./pages/Backtest";
import { Costs } from "./pages/Costs";
import { Dashboard } from "./pages/Dashboard";
import { Login } from "./pages/Login";
import { Logs } from "./pages/Logs";
import { Portfolio } from "./pages/Portfolio";
import { Settings } from "./pages/Settings";
import { Stock } from "./pages/Stock";
import { TradingRoom } from "./pages/TradingRoom";

function Routed() {
  const { token, authEnabled, ready } = useAuth();
  if (!ready) return null;
  if (authEnabled && !token) return <Login />;
  return (
    <EventsProvider key={token ?? "local"}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="room" element={<TradingRoom />} />
          <Route path="agent" element={<AgentPage />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="stock/:symbol" element={<Stock />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="settings" element={<Settings />} />
          <Route path="costs" element={<Costs />} />
          <Route path="logs" element={<Logs />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </EventsProvider>
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
    </QueryClientProvider>
  );
}
