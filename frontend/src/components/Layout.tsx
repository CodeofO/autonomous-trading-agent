import {
  memo,
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { MarketCode, MarketView, SystemInfo } from "../api/types";
import { useAuth } from "../auth";
import { useSystem } from "../hooks";
import { useConnected, useEventsStore } from "../live/hooks";
import { MARKET_META, readStore, useMarket, writeStore } from "../market";
import { isMobile, prefersReducedMotion, useIsMobile, useMobileViewport } from "../mobile";
import { preloadPage } from "../routes";
import { openPalette, preloadPalette } from "../ui/overlays";
import { usePullToRefresh, type PullState } from "../ui/usePullToRefresh";
import { llmChipText } from "../utils/llm";
import { ConnectionBanner } from "./ConnectionBanner";
import { KillSwitch } from "./KillSwitch";
import { Loading } from "./Loading";
import { MarketSwitch } from "./MarketSwitch";
import { CommandPaletteHost, StockSheetHost } from "./Overlays";
import { RouteErrorBoundary } from "./RouteErrorBoundary";
import { isActive, MORE_ITEMS, NAV_GROUPS, SETTINGS, TAB_ITEMS, TABS, tabIndex, type NavItem } from "./nav";
import { Sheet } from "./Sheet";

export function PaperBadge({ live = false }: { live?: boolean }) {
  if (live) {
    return (
      <div className="paper-badge live" role="status" aria-label="실거래 모드">
        <span className="dot" />
        실거래 <b>LIVE</b>
      </div>
    );
  }
  return (
    <div className="paper-badge" role="status" aria-label="모의투자 모드">
      <span className="dot" />
      모의투자 <b>PAPER</b>
    </div>
  );
}

const STATUS_KO: Record<string, string> = { RUNNING: "실행 중", PAUSED: "일시정지", KILLED: "정지(킬)" };

/** 휴대폰에서 당겨서 새로고침하는 화면 (목록·숫자를 보는 화면 — 입력 폼이 많은 설정·메모·백테스트는 빼고) */
const PULL_ROUTES = ["/room", "/news", "/portfolio", "/agent", "/logs", "/stock"];
const canPull = (pathname: string) =>
  pathname === "/" || PULL_ROUTES.some((p) => pathname === p || pathname.startsWith(`${p}/`));

/** 넓은 화면 ⌘K, 그 밖은 Ctrl+K */
const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform ?? "");
const STATUS_RANK: Record<string, number> = { RUNNING: 0, PAUSED: 1, KILLED: 2 };

/** 여러 시장 중 가장 나쁜 엔진 상태 (색을 정하는 데 쓴다) */
const worst = (list: { sys: SystemInfo }[]) =>
  list.reduce<string>(
    (w, x) => ((STATUS_RANK[x.sys.engine_status] ?? 0) > (STATUS_RANK[w] ?? 0) ? x.sys.engine_status : w),
    "RUNNING",
  );

/** 엔진 상태 한 줄: 한 시장이면 '엔진 실행 중', 전체 보기에서 시장마다 다르면 '국장 실행 중 · 미장 정지(킬)' */
function engineText(list: { code: MarketCode; sys: SystemInfo }[]): string {
  const ko = (s: string) => STATUS_KO[s] ?? s;
  const same = list.every((x) => x.sys.engine_status === list[0].sys.engine_status);
  if (same) return `엔진 ${ko(list[0].sys.engine_status)}`;
  return list.map((x) => `${MARKET_META[x.code].name} ${ko(x.sys.engine_status)}`).join(" · ");
}

export function Layout() {
  const { view, market, available } = useMarket();
  const all = view === "ALL";
  const hasUS = available.includes("US");
  // 두 시장 상태를 다 받아 둔다 — 시장을 바꿔도 상단 바가 비었다가 채워지며 튀지 않게 (안 보는 시장은 30초마다).
  // 웹소켓이 연결돼 있으면 상태 이벤트가 바로 알려 주므로 30초·60초로 늦춘다 (hooks.ts useSystem)
  const krSys = useSystem("KR", { refetchInterval: all || market === "KR" ? 5000 : 30_000 });
  const usSys = useSystem("US", { enabled: hasUS, refetchInterval: all || market === "US" ? 5000 : 30_000 });
  const sys = market === "US" ? usSys.data : krSys.data;
  // 전체 보기의 칩(토스 연결·LLM·매니저 이름)은 국장 것 — 토스 연결은 같고, LLM 설정 카드도 국장(공통) 기준
  const top = all ? krSys.data : sys;
  const shown: { code: MarketCode; sys: SystemInfo }[] = all
    ? [
        ...(krSys.data ? [{ code: "KR" as const, sys: krSys.data }] : []),
        ...(hasUS && usSys.data ? [{ code: "US" as const, sys: usSys.data }] : []),
      ]
    : sys
      ? [{ code: market, sys }]
      : [];
  const live = shown.some((x) => x.sys.trading_mode === "live");
  // 전체 보기의 킬 스위치는 아직 도는 시장을 한꺼번에 멈춘다 (모두 멈춰 있으면 모두 재개)
  const allKilled = shown.every((x) => x.sys.engine_status === "KILLED");
  const killTargets = shown.filter((x) => allKilled || x.sys.engine_status !== "KILLED").map((x) => x.code);
  const engine = shown.length > 0 ? engineText(shown) : null;
  // 연결 상태만 구독 — 채팅·진행 이벤트가 와도 머리말·메뉴는 다시 그리지 않는다
  const krLive = useConnected("KR");
  const usLive = useConnected("US");
  const connected = all ? krLive && (!hasUS || usLive) : market === "US" ? usLive : krLive;
  const { logout, authEnabled } = useAuth();
  const { pathname } = useLocation();
  const active = tabIndex(pathname);
  const mobile = useIsMobile();
  const [moreOpen, setMoreOpen] = useState(false);
  const openMore = useCallback(() => setMoreOpen(true), []);
  // 시장을 바꾸면 같은 주소라도 다른 화면 — 화면 상태와 스크롤 위치를 시장마다 따로 둔다
  const screen = `${view}:${pathname}`;
  const { scroller, topbar, onTab } = useMobileNav(pathname, screen, active);
  useMobileViewport();
  const reserved = useReservedWidths(
    topbar,
    view,
    top && engine ? `${JSON.stringify(top.llm)}|${engine}|${allKilled}` : null,
  );
  const pull = useRefreshOnPull(scroller, canPull(pathname), all ? available : [market]);
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">{top?.persona.emoji ?? "🦉"}</span>
          <div>
            <b>AI 펀드매니저</b>
            <small>Paper Trading Agent</small>
          </div>
        </div>
        {/* 넓은 화면은 묶음 메뉴, 휴대폰은 하단 탭바(4칸 + 더보기) — 한쪽만 그린다 */}
        {mobile ? (
          <TabBar active={active} view={view} onTab={onTab} onMore={openMore} moreOpen={moreOpen} />
        ) : (
          <SideNav pathname={pathname} view={view} />
        )}
        <div className="sidebar-foot">
          <span className={`conn ${connected ? "on" : ""}`}>
            {connected ? "● 실시간 연결" : "○ 연결 대기"}
          </span>
          {authEnabled && (
            <button className="link" onClick={logout}>
              로그아웃
            </button>
          )}
        </div>
      </aside>
      <div className="main">
        <header className="topbar" ref={topbar}>
          {/* 모바일 전용: 사이드바가 하단 탭바로 바뀌므로 로고·연결 상태를 위에 둔다 */}
          <span className="top-brand" aria-hidden>
            {top?.persona.emoji ?? "🦉"}
          </span>
          <PaperBadge live={live} />
          <span
            className={`conn-dot ${connected ? "on" : ""}`}
            title={connected ? "실시간 연결" : "연결 대기"}
            aria-label={connected ? "실시간 연결" : "연결 대기"}
            role="status"
          />
          <MarketSwitch />
          <div className="top-info">
            <KstClock />
            {top ? (
              <>
                {/* 토스 시세는 문제가 있을 때만 (정상이면 칩을 두지 않는다 — 아래 배너도 함께 뜬다) */}
                {!top.toss_ok && (
                  <span className="chip warn" title={top.toss_issue ?? ""} data-chip="toss">
                    토스 시세 연결 안 됨
                  </span>
                )}
                <Link
                  to="/settings/llm"
                  className={`chip chip-link ${top.llm.configured ? "ok" : "warn"}`}
                  title="LLM 모델 바꾸기 (설정)"
                  data-testid="llm-chip"
                  data-chip="llm"
                >
                  <span>{llmChipText(top.llm)}</span>
                </Link>
              </>
            ) : (
              // 상태를 받기 전에도 칩 자리를 잡아 둔다 (지난번 너비로) — 도착하면서 줄이 바뀌어 본문이 밀리지 않게
              <TopSkel className="chip top-skel llm" width={reserved?.llm} />
            )}
            {shown.length > 0 ? (
              <span
                className={`status-pill ${worst(shown).toLowerCase()}`}
                data-testid="engine-status"
                data-chip="engine"
              >
                {engine}
              </span>
            ) : (
              <TopSkel className="status-pill top-skel engine" width={reserved?.engine} />
            )}
          </div>
          <SearchButton />
          {shown.length > 0 ? (
            <KillSwitch status={allKilled ? "KILLED" : "RUNNING"} targets={all ? killTargets : undefined} />
          ) : (
            <TopSkel className="btn kill top-skel" width={reserved?.kill} />
          )}
          <SettingsButton active={isActive(SETTINGS, pathname)} view={view} />
          {/* 실시간 연결이 끊긴 동안만 상단 바 아래에 겹쳐 붙는 띠 (본문을 밀지 않는다) */}
          <ConnectionBanner />
        </header>
        {top && !top.toss_ok && (
          <div className="banner warn" role="alert">
            ⚠️ {top.toss_issue ?? "토스증권 시세를 받을 수 없어요."} — 연결될 때까지 매니저는 판단을 쉬어요.
          </div>
        )}
        {/* 넓은 화면에서는 .page-scroll·.page 가 상자 없이(display: contents) 지나가고 .content 가 스크롤한다.
            휴대폰에서는 .page-scroll 이 유일한 스크롤 영역(본문 + 하단 고지) */}
        <div className="page-scroll" ref={scroller} data-testid="page-scroll">
          <PullIndicator state={pull} />
          <main className="content">
            {/* 화면 조각을 받는 동안: 처음에는 자리 표시, 화면을 옮길 때는(라우터 전환) 받을 때까지 지금 화면을 그대로 둔다 */}
            <Suspense fallback={<RouteFallback />}>
              {/* 시장이 바뀌면 화면을 새로 그린다 — 한 시장의 고른 세션·입력값이 다른 시장 화면에 남지 않게 */}
              <Page key={`${view}:${pathname.split("/")[1]}`}>
                <RouteErrorBoundary>
                  <Outlet />
                </RouteErrorBoundary>
              </Page>
            </Suspense>
          </main>
          <footer className="disclaimer">
            {shown.length > 0
              ? shown.map((x) => <p key={x.code}>{x.sys.disclaimer}</p>)
              : "모의투자 전용 시스템입니다. 투자 권유가 아닙니다."}
          </footer>
        </div>
      </div>
      <CommandPaletteHost />
      <StockSheetHost />
      {moreOpen && (
        <MoreSheet
          pathname={pathname}
          view={view}
          onClose={() => setMoreOpen(false)}
          onLogout={authEnabled ? logout : undefined}
        />
      )}
    </div>
  );
}

/** 상단 바 🔍 — 빠른 이동(⌘K). 넓은 화면은 단축키도 보여 준다 */
function SearchButton() {
  const [key] = useState(() => (isMac() ? "⌘K" : "Ctrl K"));
  return (
    <button
      type="button"
      className="top-search"
      onClick={openPalette}
      onPointerEnter={preloadPalette}
      onFocus={preloadPalette}
      onTouchStart={preloadPalette}
      aria-label="빠른 이동 · 종목 검색"
      aria-keyshortcuts="Meta+K Control+K"
      title={`빠른 이동 · 종목 검색 (${key})`}
    >
      <span aria-hidden>🔍</span>
      <span className="top-search-text">검색</span>
      <kbd className="top-search-key">{key}</kbd>
    </button>
  );
}

/**
 * 휴대폰 '당겨서 새로고침': 지금 화면이 쓰는 데이터(보이는 쿼리)를 다시 받고,
 * 채팅·감사 로그처럼 실시간 저장소가 들고 있는 기록도 서버와 다시 맞춘다
 */
function useRefreshOnPull(
  scroller: { readonly current: HTMLDivElement | null },
  enabled: boolean,
  markets: MarketCode[],
): PullState {
  const qc = useQueryClient();
  const store = useEventsStore();
  const key = markets.join(",");
  const refresh = useCallback(
    () =>
      Promise.all([
        qc.refetchQueries({ type: "active" }),
        ...key.split(",").map((m) => store.stream(m as MarketCode).resync()),
      ]),
    [qc, store, key],
  );
  return usePullToRefresh(scroller, enabled, refresh);
}

/** 당기는 동안 맨 위에 내려오는 동그라미 (휴대폰에서만 보인다) */
function PullIndicator({ state }: { state: PullState }) {
  return (
    <div className={`ptr ${state}`} data-testid="ptr" aria-hidden={state !== "refreshing"}>
      <span className="ptr-ball">
        <span className="ptr-icon" aria-hidden>
          ↓
        </span>
      </span>
      {state === "refreshing" && <span className="sr-only">새로고침 중…</span>}
    </div>
  );
}

interface ChipWidths {
  llm: number;
  engine: number;
  kill: number;
}

const WIDTHS_KEY = "topbar.widths";

/**
 * 보기(전체·국장·미장)마다 마지막으로 그린 상단 바 칩·엔진 상태·킬 스위치의 너비(px).
 * 다음에 열 때 상태를 받기 전 자리 표시가 같은 너비를 차지해, 칩이 도착하며 줄이 바뀌어 본문이 밀리지 않는다.
 * (글자 대신 너비만 저장한다 — 모델 주소 같은 내용은 저장소에 남기지 않는다)
 */
function useReservedWidths(
  bar: { readonly current: HTMLElement | null },
  view: MarketView,
  contentKey: string | null,
): ChipWidths | null {
  const [saved] = useState<Partial<Record<MarketView, ChipWidths>>>(() => {
    try {
      return JSON.parse(readStore(WIDTHS_KEY) ?? "{}") ?? {};
    } catch {
      return {};
    }
  });
  useLayoutEffect(() => {
    const el = bar.current;
    if (!contentKey || !el) return;
    const w = (sel: string) => Math.round(el.querySelector(sel)?.getBoundingClientRect().width ?? 0);
    const next: ChipWidths = {
      llm: w('[data-chip="llm"]'),
      engine: w('[data-chip="engine"]'),
      kill: w(":scope > .btn.kill, :scope > .btn.resume"),
    };
    if (Object.values(next).some((x) => !x)) return; // 아직 그려지지 않음(또는 테스트 환경)
    const prev = saved[view];
    if (prev && (Object.keys(next) as (keyof ChipWidths)[]).every((k) => prev[k] === next[k])) return;
    saved[view] = next;
    writeStore(WIDTHS_KEY, JSON.stringify(saved));
  }, [bar, saved, view, contentKey]);
  return saved[view] ?? null;
}

/** 상단 바 자리 표시: 보이지 않고 자리만 차지한다 (기억한 너비가 없으면 styles.css 의 흔한 너비) */
function TopSkel({ className, width }: { className: string; width?: number }) {
  return (
    <span className={className} style={width ? { width } : undefined} aria-hidden>
      {"\u00a0"}
    </span>
  );
}

/** 화면 조각을 처음 받는 동안 (본문 자리를 채워 아래 고지가 튀지 않게, 잠깐이면 보이지 않게 늦게 나타난다) */
function RouteFallback() {
  return (
    <div className="route-fallback">
      <Loading />
    </div>
  );
}

/** 메뉴에 마우스·손가락이 닿으면 그 화면 조각을 미리 받는다 */
function warmProps(to: string, view: MarketView) {
  const warm = () => preloadPage(to, view);
  return { onPointerEnter: warm, onFocus: warm, onTouchStart: warm };
}

/**
 * 넓은 화면 왼쪽 메뉴: '매일 보기'·'매니저·분석' 두 묶음. 설정은 오른쪽 위 ⚙️ 로 옮겼다.
 * 상태 폴링·실시간 이벤트로 머리말이 다시 그려져도 메뉴는 그대로 둔다(memo — 주소가 바뀔 때만).
 */
const SideNav = memo(function SideNav({ pathname, view }: { pathname: string; view: MarketView }) {
  return (
    <nav className="side-nav" aria-label="메뉴">
      {NAV_GROUPS.map((g) => (
        <div key={g.title} className="nav-group" role="group" aria-label={g.title}>
          <span className="nav-group-title" aria-hidden>
            {g.title}
          </span>
          {g.items.map((n) => {
            const on = isActive(n, pathname);
            return (
              <Link
                key={n.to}
                to={n.to}
                aria-label={n.label}
                aria-current={on ? "page" : undefined}
                className={on ? "active" : undefined}
                {...warmProps(n.to, view)}
              >
                <span className="nav-icon" aria-hidden>
                  {n.icon}
                </span>{" "}
                <span className="nav-label">{n.label}</span>
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
});

/**
 * 휴대폰 하단 탭바: 자주 보는 4칸 + 더보기(나머지 화면·설정을 아래 시트로).
 * 선택된 탭 뒤의 표시(--tab-i)가 미끄러지듯 옮겨 다닌다.
 */
const TabBar = memo(function TabBar({
  active,
  view,
  onTab,
  onMore,
  moreOpen,
}: {
  active: number;
  view: MarketView;
  onTab: (e: MouseEvent<HTMLAnchorElement>, to: string, i: number) => void;
  onMore: () => void;
  moreOpen: boolean;
}) {
  const moreActive = active === TABS.length - 1;
  return (
    <nav
      className="tab-bar"
      aria-label="하단 탭"
      style={{ "--tab-i": active, "--tabs": TABS.length } as CSSProperties}
    >
      {active >= 0 && <span className="tab-ind" aria-hidden />}
      {TAB_ITEMS.map((n, i) => (
        <Link
          key={n.to}
          to={n.to}
          aria-label={n.label}
          aria-current={active === i ? "page" : undefined}
          className={active === i ? "active" : undefined}
          onClick={(e) => onTab(e, n.to, i)}
          {...warmProps(n.to, view)}
        >
          <span className="nav-icon" aria-hidden>
            {n.icon}
          </span>
          <span className="nav-short" aria-hidden>
            {n.short}
          </span>
        </Link>
      ))}
      <button
        type="button"
        className={`tab-more ${moreActive ? "active" : ""}`}
        aria-label="더보기"
        aria-haspopup="dialog"
        aria-expanded={moreOpen}
        aria-current={moreActive ? "page" : undefined}
        onClick={onMore}
      >
        <span className="nav-icon" aria-hidden>
          ☰
        </span>
        <span className="nav-short" aria-hidden>
          더보기
        </span>
      </button>
    </nav>
  );
});

/** 휴대폰 '더보기' 시트: 매니저·백테스트·이벤트 로그·LLM 비용·설정 (+ 로그아웃) */
function MoreSheet({
  pathname,
  view,
  onClose,
  onLogout,
}: {
  pathname: string;
  view: MarketView;
  onClose: () => void;
  onLogout?: () => void;
}) {
  return (
    <Sheet label="더보기" title="더보기" onClose={onClose} className="more-sheet">
      <ul className="more-list">
        {MORE_ITEMS.map((n: NavItem) => {
          const on = isActive(n, pathname);
          return (
            <li key={n.to}>
              <Link
                to={n.to}
                className={`more-item press ${on ? "active" : ""}`}
                aria-current={on ? "page" : undefined}
                onClick={onClose}
                {...warmProps(n.to, view)}
              >
                <span className="more-icon" aria-hidden>
                  {n.icon}
                </span>
                <span className="more-text">
                  <b>{n.label}</b>
                  {n.hint && <small className="muted">{n.hint}</small>}
                </span>
                <span className="settings-chev" aria-hidden>
                  ›
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
      {onLogout && (
        <button
          type="button"
          className="btn ghost more-logout"
          onClick={() => {
            onClose();
            onLogout();
          }}
        >
          로그아웃
        </button>
      )}
    </Sheet>
  );
}

/** 오른쪽 위 ⚙️ 설정 (넓은 화면은 글자도) */
function SettingsButton({ active, view }: { active: boolean; view: MarketView }) {
  return (
    <Link
      to={SETTINGS.to}
      className={`top-settings ${active ? "active" : ""}`}
      aria-label="설정"
      aria-current={active ? "page" : undefined}
      title="설정"
      {...warmProps(SETTINGS.to, view)}
    >
      <span aria-hidden>⚙️</span>
      <span className="top-settings-text">설정</span>
    </Link>
  );
}

/** 앱을 연 뒤 첫 화면을 이미 그렸는지 — 첫 화면은 등장 효과 없이 바로 (첫 화면 표시가 늦어지지 않게) */
let firstPageShown = false;

/**
 * 화면 하나. 탭·시장 전환(View Transition)이 아닌 이동이면 등장 효과(.page.enter):
 * 휴대폰은 살짝 떠오르고, 넓은 화면은 짧게 흐려졌다 또렷해진다
 */
function Page({ children }: { children: ReactNode }) {
  // 처음 그릴 때 한 번만 정한다 — 전환이 끝난 뒤 애니메이션이 다시 돌지 않게
  const [enter] = useState(() => {
    const root = document.documentElement.classList;
    return firstPageShown && !root.contains("vt-nav") && !root.contains("vt-mkt");
  });
  useEffect(() => {
    firstPageShown = true;
  }, []);
  return <div className={enter ? "page enter" : "page"}>{children}</div>;
}

/**
 * 휴대폰 화면의 탭 이동을 앱처럼 (넓은 화면에서는 아무것도 하지 않는다)
 * - 탭마다 스크롤 위치를 기억했다가 돌아오면 복원
 * - 탭 전환은 View Transitions 로 본문만 살짝 밀며 바꾸고, 탭 표시는 새 위치로 미끄러진다
 * - 이미 보고 있는 탭을 다시 누르면 맨 위로
 * - 본문을 내리면 상단 바에 그림자
 */
function useMobileNav(pathname: string, screen: string, active: number) {
  const navigate = useNavigate();
  const scroller = useRef<HTMLDivElement>(null);
  const topbar = useRef<HTMLElement>(null);
  const positions = useRef(new Map<string, number>());
  const current = useRef(screen);
  const navDone = useRef<(() => void) | null>(null);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      positions.current.set(current.current, el.scrollTop);
      topbar.current?.classList.toggle("scrolled", el.scrollTop > 2);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    // 보이는 본문 높이 — 트레이딩 룸 채팅이 상단 바와 탭바 사이를 꽉 채우는 데 쓴다
    const ro =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => el.style.setProperty("--scroll-h", `${el.clientHeight}px`));
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, []);

  // 화면이 바뀐 직후(그리기 전)에 그 탭의 스크롤 위치를 되돌린다. 내용이 아직 짧으면 몇 프레임 더 시도
  useLayoutEffect(() => {
    current.current = screen;
    const el = scroller.current;
    let frame = 0;
    const stop = () => cancelAnimationFrame(frame);
    if (el && isMobile()) {
      const target = positions.current.get(screen) ?? 0;
      const started = performance.now();
      const apply = () => {
        el.scrollTop = target;
        topbar.current?.classList.toggle("scrolled", el.scrollTop > 2);
        if (Math.abs(el.scrollTop - target) > 1 && performance.now() - started < 800)
          frame = requestAnimationFrame(apply);
      };
      apply();
      // 손가락이 닿으면 복원을 멈춘다 (사용자 스크롤과 싸우지 않게)
      el.addEventListener("touchstart", stop, { passive: true });
      el.addEventListener("wheel", stop, { passive: true });
    }
    navDone.current?.();
    navDone.current = null;
    return () => {
      stop();
      el?.removeEventListener("touchstart", stop);
      el?.removeEventListener("wheel", stop);
    };
  }, [screen]);

  // 메뉴(memo)에 넘기는 함수는 늘 같은 것 — 지금 주소·탭 위치는 ref 로 읽는다
  const latest = useRef({ pathname, active });
  useLayoutEffect(() => {
    latest.current = { pathname, active };
  });

  const onTab = useCallback(
    (e: MouseEvent<HTMLAnchorElement>, to: string, i: number) => {
      if (!isMobile() || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const { pathname, active } = latest.current;
      const smooth = !prefersReducedMotion();
      if (pathname === to) {
        e.preventDefault();
        scroller.current?.scrollTo({ top: 0, behavior: smooth ? "smooth" : "auto" });
        return;
      }
      // 지원하지 않는 브라우저는 그냥 이동 (.page 의 CSS 등장 효과만)
      if (!smooth || typeof document.startViewTransition !== "function") return;
      e.preventDefault();
      const root = document.documentElement;
      root.dataset.navDir = i < active ? "prev" : "next";
      root.classList.add("vt-nav");
      const t = document.startViewTransition(
        () =>
          new Promise<void>((resolve) => {
            navDone.current = resolve;
            navigate(to);
            window.setTimeout(resolve, 300); // 화면이 늦게 바뀌어도 전환이 멈춰 있지 않게
          }),
      );
      const done = () => {
        root.classList.remove("vt-nav");
        delete root.dataset.navDir;
      };
      t.ready.catch(() => {});
      t.finished.then(done, done);
    },
    [navigate],
  );

  return { scroller, topbar, onTab };
}

/** 실제 한국 시각 (브라우저 시계 기준, 1초마다 갱신) */
export function KstClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const text = now.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return (
    <span className="chip" title="한국 표준시(KST)" data-testid="kst-clock">
      🕒 {text} KST
    </span>
  );
}
