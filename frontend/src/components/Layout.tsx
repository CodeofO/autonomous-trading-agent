import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useEvents } from "../events";
import { useSystem } from "../hooks";
import { isMobile, navIndex, prefersReducedMotion, useMobileViewport } from "../mobile";
import { llmChipText } from "../utils/llm";
import { KillSwitch } from "./KillSwitch";

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

// short: 모바일 하단 탭바에 쓰는 짧은 이름. mobile: false 는 넓은 화면 사이드바에만 (하단 탭바는 7칸 그대로)
const NAV: { to: string; label: string; short: string; icon: string; mobile?: false }[] = [
  { to: "/", label: "대시보드", short: "홈", icon: "🏠" },
  { to: "/room", label: "트레이딩 룸", short: "채팅", icon: "💬" },
  { to: "/agent", label: "에이전트 구조", short: "구조", icon: "🗺️" },
  { to: "/portfolio", label: "포트폴리오·주문", short: "자산", icon: "📒" },
  { to: "/backtest", label: "백테스트", short: "백테스트", icon: "🧪" },
  { to: "/settings", label: "설정", short: "설정", icon: "⚙️" },
  { to: "/logs", label: "이벤트 로그", short: "로그", icon: "📜" },
  // 휴대폰에서는 설정 화면의 링크로 들어간다
  { to: "/costs", label: "LLM 비용", short: "비용", icon: "💸", mobile: false },
];
const TABS = NAV.filter((n) => n.mobile !== false).map((n) => n.to);

const STATUS_KO: Record<string, string> = { RUNNING: "실행 중", PAUSED: "일시정지", KILLED: "정지(킬)" };

export function Layout() {
  const { data: sys } = useSystem();
  const { connected } = useEvents();
  const { logout, authEnabled } = useAuth();
  const { pathname } = useLocation();
  const active = navIndex(pathname, TABS);
  const { scroller, topbar, onTab } = useMobileNav(pathname, active);
  useMobileViewport();
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">{sys?.persona.emoji ?? "🦉"}</span>
          <div>
            <b>AI 펀드매니저</b>
            <small>Paper Trading Agent</small>
          </div>
        </div>
        <nav style={{ "--tab-i": active } as CSSProperties}>
          {/* 모바일 탭바: 선택된 탭 뒤를 미끄러지듯 옮겨 다니는 표시 */}
          {active >= 0 && <span className="tab-ind" aria-hidden />}
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              aria-label={n.label}
              className={n.mobile === false ? "desktop-only" : undefined}
              onClick={(e) => onTab(e, n.to, TABS.indexOf(n.to))}
            >
              <span className="nav-icon" aria-hidden>
                {n.icon}
              </span>{" "}
              <span className="nav-label">{n.label}</span>
              <span className="nav-short" aria-hidden>
                {n.short}
              </span>
            </NavLink>
          ))}
        </nav>
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
            {sys?.persona.emoji ?? "🦉"}
          </span>
          <PaperBadge live={sys?.trading_mode === "live"} />
          <span
            className={`conn-dot ${connected ? "on" : ""}`}
            title={connected ? "실시간 연결" : "연결 대기"}
            aria-label={connected ? "실시간 연결" : "연결 대기"}
            role="status"
          />
          <div className="top-info">
            <KstClock />
            {sys && (
              <>
                <span className={`chip ${sys.toss_ok ? "ok" : "warn"}`} title={sys.toss_issue ?? ""}>
                  {sys.toss_ok ? "토스 실시세 연결" : "토스 시세 연결 안 됨"}
                </span>
                <Link
                  to="/settings"
                  className={`chip chip-link ${sys.llm.configured ? "ok" : "warn"}`}
                  title="LLM 모델 바꾸기 (설정)"
                  data-testid="llm-chip"
                >
                  <span>{llmChipText(sys.llm)}</span>
                </Link>
                <span
                  className={`status-pill ${sys.engine_status.toLowerCase()}`}
                  data-testid="engine-status"
                >
                  엔진 {STATUS_KO[sys.engine_status] ?? sys.engine_status}
                </span>
              </>
            )}
          </div>
          {sys && <KillSwitch status={sys.engine_status} />}
          {authEnabled && (
            <button className="link top-logout" onClick={logout}>
              로그아웃
            </button>
          )}
        </header>
        {sys && !sys.toss_ok && (
          <div className="banner warn" role="alert">
            ⚠️ {sys.toss_issue ?? "토스증권 시세를 받을 수 없어요."} — 연결될 때까지 매니저는 판단을 쉬어요.
          </div>
        )}
        {/* 넓은 화면에서는 .page-scroll·.page 가 상자 없이(display: contents) 지나가고 .content 가 스크롤한다.
            휴대폰에서는 .page-scroll 이 유일한 스크롤 영역(본문 + 하단 고지) */}
        <div className="page-scroll" ref={scroller} data-testid="page-scroll">
          <main className="content">
            <Page key={pathname.split("/")[1]}>
              <Outlet />
            </Page>
          </main>
          <footer className="disclaimer">
            {sys?.disclaimer ?? "모의투자 전용 시스템입니다. 투자 권유가 아닙니다."}
          </footer>
        </div>
      </div>
    </div>
  );
}

/** 화면 하나. 탭 전환(View Transition)이 아닌 이동이면 휴대폰에서 살짝 떠오르며 나타난다(.page.enter) */
function Page({ children }: { children: ReactNode }) {
  // 처음 그릴 때 한 번만 정한다 — 전환이 끝난 뒤 애니메이션이 다시 돌지 않게
  const [enter] = useState(() => !document.documentElement.classList.contains("vt-nav"));
  return <div className={enter ? "page enter" : "page"}>{children}</div>;
}

/**
 * 휴대폰 화면의 탭 이동을 앱처럼 (넓은 화면에서는 아무것도 하지 않는다)
 * - 탭마다 스크롤 위치를 기억했다가 돌아오면 복원
 * - 탭 전환은 View Transitions 로 본문만 살짝 밀며 바꾸고, 탭 표시는 새 위치로 미끄러진다
 * - 이미 보고 있는 탭을 다시 누르면 맨 위로
 * - 본문을 내리면 상단 바에 그림자
 */
function useMobileNav(pathname: string, active: number) {
  const navigate = useNavigate();
  const scroller = useRef<HTMLDivElement>(null);
  const topbar = useRef<HTMLElement>(null);
  const positions = useRef(new Map<string, number>());
  const current = useRef(pathname);
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
    current.current = pathname;
    const el = scroller.current;
    let frame = 0;
    const stop = () => cancelAnimationFrame(frame);
    if (el && isMobile()) {
      const target = positions.current.get(pathname) ?? 0;
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
  }, [pathname]);

  const onTab = (e: MouseEvent<HTMLAnchorElement>, to: string, i: number) => {
    if (!isMobile() || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
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
  };

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
