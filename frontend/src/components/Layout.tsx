import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { useEvents } from "../events";
import { useSystem } from "../hooks";
import { fmtDateTime } from "../utils/format";
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
  const { connected, simTime } = useEvents();
  const { logout } = useAuth();
  const now = simTime ?? sys?.now;
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
          <button className="link" onClick={logout}>
            로그아웃
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <PaperBadge />
          <div className="top-info">
            {now && (
              <span
                className="chip"
                title={sys?.clock_mode === "simulated" ? "가속 시뮬레이션 시계" : "실시간"}
              >
                {sys?.clock_mode === "simulated" ? "⏩ " : "🕒 "}
                {fmtDateTime(now)}
              </span>
            )}
            {sys && (
              <>
                <span className={`chip src ${sys.data_source}`}>데이터 {sys.data_source.toUpperCase()}</span>
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
