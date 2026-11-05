import type { MarketView } from "./api/types";
import { preloadCharts } from "./components/chartsLoader";
import { lazyWithPreload, type Preloadable } from "./lazyPage";
import { isMobile } from "./mobile";
import { DEFAULT_SECTION, SECTION_PAGES, sectionOf } from "./pages/settings/lazy";

/*
 * 화면마다 따로 받는 조각 (첫 화면에 필요한 코드만 먼저 받는다).
 * 차트를 그리는 화면(대시보드·종목·백테스트)은 화면 조각과 함께 차트 조각도 바로 받기 시작한다.
 */
export const OverviewPage = lazyWithPreload(
  () => import("./pages/Overview"),
  (m) => m.Overview,
);
export const DashboardPage = lazyWithPreload(
  () => {
    preloadCharts();
    return import("./pages/Dashboard");
  },
  (m) => m.Dashboard,
);
export const TradingRoomPage = lazyWithPreload(
  () => import("./pages/TradingRoom"),
  (m) => m.TradingRoom,
);
export const NewsRoomRoute = lazyWithPreload(
  () => import("./pages/NewsRoom"),
  (m) => m.NewsRoomPage,
);
export const AgentRoute = lazyWithPreload(
  () => import("./pages/Agent"),
  (m) => m.AgentPage,
);
export const MemoRoute = lazyWithPreload(
  () => import("./pages/Memo"),
  (m) => m.MemoPage,
);
export const PortfolioPage = lazyWithPreload(
  () => import("./pages/Portfolio"),
  (m) => m.Portfolio,
);
export const StockPage = lazyWithPreload(
  () => {
    preloadCharts();
    return import("./pages/Stock");
  },
  (m) => m.Stock,
);
export const BacktestPage = lazyWithPreload(
  () => {
    preloadCharts();
    return import("./pages/Backtest");
  },
  (m) => m.Backtest,
);
export const LabPage = lazyWithPreload(
  () => import("./pages/Lab"),
  (m) => m.Lab,
);
export const SettingsPage = lazyWithPreload(
  () => import("./pages/Settings"),
  (m) => m.Settings,
);
export const CostsPage = lazyWithPreload(
  () => import("./pages/Costs"),
  (m) => m.Costs,
);
export const LogsPage = lazyWithPreload(
  () => import("./pages/Logs"),
  (m) => m.Logs,
);
export const LoginPage = lazyWithPreload(
  () => import("./pages/Login"),
  (m) => m.Login,
);

const BY_PREFIX: [prefix: string, page: Preloadable][] = [
  ["/room", TradingRoomPage],
  ["/news", NewsRoomRoute],
  ["/agent", AgentRoute],
  ["/memo", MemoRoute],
  ["/portfolio", PortfolioPage],
  ["/stock", StockPage],
  ["/backtest", BacktestPage],
  ["/lab", LabPage],
  ["/settings", SettingsPage],
  ["/costs", CostsPage],
  ["/logs", LogsPage],
];

/** 이 주소·보기에서 그릴 화면 조각 ('/'는 '전체'면 한눈에 보기, 시장이면 대시보드) */
export function pageFor(pathname: string, view: MarketView): Preloadable | null {
  if (pathname === "/" || pathname === "") return view === "ALL" ? OverviewPage : DashboardPage;
  const hit = BY_PREFIX.find(([p]) => pathname === p || pathname.startsWith(`${p}/`));
  return hit ? hit[1] : null;
}

/**
 * 설정 주소가 바로 보여 줄 주제 조각: /settings/<주제> 는 그 주제,
 * /settings 는 넓은 화면이면 LLM 모델(오른쪽에 먼저 보인다), 휴대폰이면 목록뿐이라 없음
 */
function settingsSectionFor(pathname: string): Preloadable | null {
  const sec = sectionOf(pathname);
  if (sec) return SECTION_PAGES[sec];
  if ((pathname === "/settings" || pathname === "/settings/") && !isMobile())
    return SECTION_PAGES[DEFAULT_SECTION];
  return null;
}

/** 곧 열 화면을 미리 받는다 (실패는 무시 — 실제로 열 때 다시 시도하고 오류 화면을 보여 준다) */
export function preloadPage(pathname: string, view: MarketView): void {
  pageFor(pathname, view)
    ?.preload()
    .catch(() => {});
  // 설정 화면은 화면 조각과 주제 조각을 함께 받는다 (차례로 받으면 한 번 더 기다린다)
  settingsSectionFor(pathname)
    ?.preload()
    .catch(() => {});
}

const ALL_PAGES: Preloadable[] = [OverviewPage, DashboardPage, ...BY_PREFIX.map(([, p]) => p)];

/**
 * 첫 화면이 다 뜨고 브라우저가 한가할 때 나머지 화면 조각을 차례로 받아 둔다 — 메뉴·시장 전환이 바로 열린다.
 * 데이터 절약 모드·느린 2G 에서는 하지 않는다 (그때는 메뉴에 손가락이 닿을 때 받는다)
 */
export function prefetchPagesWhenIdle(delayMs = 2500): () => void {
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } })
    .connection;
  if (conn?.saveData || /(^|-)2g$/.test(conn?.effectiveType ?? "")) return () => {};
  let stopped = false;
  let idle: number | undefined;
  const run = async () => {
    for (const p of ALL_PAGES) {
      if (stopped) return;
      await p.preload().catch(() => {});
    }
    // 휴대폰: 확인 창(킬 스위치·유료 전환)의 아래 시트도
    if (!stopped && isMobile()) await import("./components/Sheet").catch(() => {});
  };
  const timer = window.setTimeout(() => {
    if (typeof window.requestIdleCallback === "function")
      idle = window.requestIdleCallback(() => void run(), { timeout: 5000 });
    else void run();
  }, delayMs);
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    if (idle !== undefined) window.cancelIdleCallback(idle);
  };
}
