import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { useEvents } from "../events";
import { useSystem } from "../hooks";
import { KillSwitch } from "./KillSwitch";

export function PaperBadge() {
  return (
    <div className="paper-badge" role="status" aria-label="모의투자 모드">
      <span className="dot" />
      모의투자 <b>PAPER</b>
    </div>
  );
}

const NAV = [
  { to: "/", label: "대시보드", icon: "🏠" },
  { to: "/room", label: "트레이딩 룸", icon: "💬" },
  { to: "/portfolio", label: "포트폴리오·주문", icon: "📒" },
  { to: "/backtest", label: "백테스트", icon: "🧪" },
  { to: "/settings", label: "설정", icon: "⚙️" },
  { to: "/logs", label: "이벤트 로그", icon: "📜" },
];

const STATUS_KO: Record<string, string> = { RUNNING: "실행 중", PAUSED: "일시정지", KILLED: "정지(킬)" };

export function Layout() {
  const { data: sys } = useSystem();
  const { connected } = useEvents();
  const { logout, authEnabled } = useAuth();
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
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}>
              <span aria-hidden>{n.icon}</span> {n.label}
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
        <header className="topbar">
          <PaperBadge />
          <div className="top-info">
            <KstClock />
            {sys && (
              <>
                <span className={`chip ${sys.toss_ok ? "ok" : "warn"}`} title={sys.toss_issue ?? ""}>
                  {sys.toss_ok ? "토스 실시세 연결" : "토스 시세 연결 안 됨"}
                </span>
                <span className={`chip ${sys.llm.configured ? "ok" : "warn"}`}>
                  {sys.llm.configured ? `LLM ${sys.llm.model}` : "LLM 미설정(규칙)"}
                </span>
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
        </header>
        {sys && !sys.toss_ok && (
          <div className="banner warn" role="alert">
            ⚠️ {sys.toss_issue ?? "토스증권 시세를 받을 수 없어요."} — 연결될 때까지 매니저는 판단을 쉬어요.
          </div>
        )}
        <main className="content">
          <Outlet />
        </main>
        <footer className="disclaimer">
          {sys?.disclaimer ?? "모의투자 전용 시스템입니다. 투자 권유가 아닙니다."}
        </footer>
      </div>
    </div>
  );
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
