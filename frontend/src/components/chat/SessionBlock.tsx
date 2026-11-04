import { memo, useId, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { AgentProgress, ChatMsg } from "../../api/types";
import { ACTION_KO, fmtDateTime, fmtNum, fmtTime } from "../../utils/format";
import { DecisionCard, ToolChip } from "./ChatMessage";
import { fmtDuration, groupTools, SOURCE_CLS, STATUS_LABEL, TRIGGER_KO, type SessionView } from "./sessions";
import "./chat-session.css";

interface Props {
  view: SessionView;
  expanded: boolean;
  /** 펼치기/접기 — (세션 번호, 펼칠지). 목록 전체가 같은 함수를 쓴다 */
  onToggle: (runId: number, open: boolean) => void;
  /** 진행 중인 세션에만 (나머지는 null — 진행 이벤트마다 다시 그리지 않게) */
  progress?: AgentProgress | null;
  showTools?: boolean;
}

// 유지 결정이 이보다 많으면 이름만 한 줄로 모으고 카드는 펼쳐야 보인다
const HOLD_FOLD = 3;

const LEAD_EMOJI = /^(\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*)\s*/u;

function sameDay(iso: string): boolean {
  const day = (d: Date) => d.toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" });
  return day(new Date(iso)) === day(new Date());
}

/**
 * 판단 세션 하나: 머리줄(번호·시각·상태·소요·도구 수) + 시장 판단 + 결정 + 확인한 정보(접힘).
 * 요약(view)이 같은 객체면 다시 그리지 않는다 (ChatFeed 가 세션별로 기억해 둔다)
 */
export const SessionBlock = memo(function SessionBlock({
  view,
  expanded,
  onToggle,
  progress,
  showTools = true,
}: Props) {
  const bodyId = useId();
  const v = view;
  const duration = fmtDuration(v.durationMs);
  const started = v.startedAt
    ? sameDay(v.startedAt)
      ? fmtTime(v.startedAt)
      : fmtDateTime(v.startedAt)
    : null;
  const trigger = v.trigger ? (TRIGGER_KO[v.trigger] ?? v.trigger) : null;
  const holds = v.decisions.filter((d) => d.payload.action !== "BUY" && d.payload.action !== "SELL");
  const trades = v.decisions.filter((d) => d.payload.action === "BUY" || d.payload.action === "SELL");

  return (
    <section
      className={`session ${v.status} ${expanded ? "open" : ""}`}
      data-testid="session"
      aria-label={`판단 세션 #${v.runId}`}
    >
      <button
        type="button"
        className="session-head"
        onClick={() => onToggle(v.runId, !expanded)}
        aria-expanded={expanded}
        aria-controls={expanded ? bodyId : undefined}
      >
        <span className="session-title">
          <b>세션 #{v.runId}</b>
          {trigger && <span className="session-trigger">{trigger}</span>}
        </span>
        <span className={`session-status ${v.status}`} data-testid="session-status">
          {v.status === "running" && <span className="live-dot" aria-hidden />}
          {STATUS_LABEL[v.status]}
        </span>
        <span className="session-meta">
          {started && <span>{started} 시작</span>}
          {duration && <span>{duration}</span>}
          <span>도구 {v.tools.length}회</span>
        </span>
        <span className="session-caret" aria-hidden>
          {expanded ? "접기 ▴" : "펼치기 ▾"}
        </span>
      </button>

      <div className="session-body" id={bodyId}>
        {v.status === "running" && <LiveLine view={v} progress={progress} showTools={showTools} />}
        {v.status === "failed" && <FailedBox error={v.error} />}

        {expanded ? (
          <>
            {(v.marketView || v.summary) && (
              <Section title="시장 판단" icon="📊">
                {v.marketView && (
                  <p className="session-text">
                    <span className="session-tag">🌏 시장 관점</span>
                    {v.marketView}
                  </p>
                )}
                {v.summary && (
                  <p className="session-text">
                    <span className="session-tag">🏁 정리</span>
                    {v.summary}
                  </p>
                )}
              </Section>
            )}
            {(v.decisions.length > 0 || v.risk.length > 0 || v.status === "done") && (
              <Section title="결정" icon="✅" extra={<Counts view={v} />}>
                {v.decisions.length === 0 && (
                  <p className="muted session-empty">이번 세션에는 결정이 없어요.</p>
                )}
                {(holds.length > HOLD_FOLD ? trades : v.decisions).map((d) => (
                  <DecisionCard key={d.id} msg={d} />
                ))}
                {holds.length > HOLD_FOLD && <HoldFold holds={holds} />}
                <RiskNotes risk={v.risk} />
              </Section>
            )}
            {showTools && v.tools.length > 0 && <ToolsSection view={v} />}
            {v.notes.length > 0 && <NotesSection notes={v.notes} />}
          </>
        ) : (
          <>
            {(v.summary || v.marketView) && (
              <p className="session-text clamp" data-testid="session-summary">
                {v.summary || v.marketView}
              </p>
            )}
            {v.decisions.length > 0 && <CompactDecisions decisions={v.decisions} />}
            <RiskNotes risk={v.risk.filter((r) => r.kind === "block")} />
          </>
        )}
      </div>
    </section>
  );
});

function Section({
  title,
  icon,
  extra,
  children,
}: {
  title: string;
  icon: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="session-sec">
      <div className="session-sec-title">
        <span className="session-icon" aria-hidden>
          {icon}
        </span>
        {title}
        {extra}
      </div>
      <div className="session-sec-body">{children}</div>
    </div>
  );
}

function Counts({ view }: { view: SessionView }) {
  const c = view.counts;
  const n = (a: string) => c?.[a] ?? view.decisions.filter((d) => d.payload.action === a).length;
  if (!c && view.decisions.length === 0) return null;
  return (
    <span className="counts session-counts">
      <span className="pill buy">매수 {n("BUY")}</span>
      <span className="pill sell">매도 {n("SELL")}</span>
      <span className="pill hold">유지 {n("HOLD")}</span>
    </span>
  );
}

const decName = (d: ChatMsg) => String(d.payload.name || d.payload.symbol || d.content);

function HoldFold({ holds }: { holds: ChatMsg[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="session-holds">
      <button
        type="button"
        className="session-toggle"
        onClick={() => setOpen((x) => !x)}
        aria-expanded={open}
      >
        <span className="action-badge hold">{ACTION_KO.HOLD}</span>
        <span className="session-holds-names">
          {holds.length}종목 · {holds.map(decName).join(", ")}
        </span>
        <span className="session-caret">{open ? "접기 ▴" : "근거 보기 ▾"}</span>
      </button>
      {open && holds.map((d) => <DecisionCard key={d.id} msg={d} />)}
    </div>
  );
}

function CompactDecisions({ decisions }: { decisions: ChatMsg[] }) {
  const trades = decisions.filter((d) => d.payload.action === "BUY" || d.payload.action === "SELL");
  const holds = decisions.filter((d) => !trades.includes(d));
  return (
    <ul className="session-decs" data-testid="session-decisions">
      {trades.map((d) => {
        const action = String(d.payload.action);
        const qty = Number(d.payload.quantity) || 0;
        const conf = Math.round((Number(d.payload.confidence) || 0) * 100);
        return (
          <li key={d.id}>
            <span className={`action-badge ${action.toLowerCase()}`}>{ACTION_KO[action]}</span>
            <Link to={`/stock/${String(d.payload.symbol ?? "")}`}>{decName(d)}</Link>
            <small className="muted">
              {qty ? `${fmtNum(qty)}주 · ` : ""}확신도 {conf}%
            </small>
          </li>
        );
      })}
      {holds.length > 0 && (
        <li>
          <span className="action-badge hold">{ACTION_KO.HOLD}</span>
          <span className="session-holds-names">
            {holds.length}종목 · {holds.map(decName).join(", ")}
          </span>
        </li>
      )}
    </ul>
  );
}

function RiskNotes({ risk }: { risk: ChatMsg[] }) {
  if (risk.length === 0) return null;
  return (
    <ul className="session-risk">
      {risk.map((r) => (
        <li key={r.id} className={r.kind}>
          <span aria-hidden>🛡️</span> {r.content}
        </li>
      ))}
    </ul>
  );
}

function FailedBox({ error }: { error: string | null }) {
  return (
    <div className="session-fail" data-testid="session-fail">
      <p>⚠️ 중간에 문제가 생겨 멈췄어요. 이번 세션 주문은 내지 않았어요.</p>
      {error && (
        <details>
          <summary>오류 내용 보기</summary>
          <code>{error}</code>
        </details>
      )}
    </div>
  );
}

/** 진행 중: 지금 하는 일 한 줄 + 방금 확인한 도구 + 최근 매니저 생각 (모든 줄을 늘어놓지 않는다) */
function LiveLine({
  view,
  progress,
  showTools,
}: {
  view: SessionView;
  progress?: AgentProgress | null;
  showTools: boolean;
}) {
  const p = progress?.active && progress.run_id === view.runId ? progress : null;
  const raw = p?.label ?? "";
  const lead = raw.match(LEAD_EMOJI);
  const label = (lead ? raw.slice(lead[0].length) : raw) || "작업 중…";
  const lastTool = view.tools[view.tools.length - 1];
  const lastNote = [...view.notes].reverse().find((n) => n.role === "analyst");
  return (
    <div className="session-live" data-testid="session-live">
      <div className="session-live-now" role="status">
        <span aria-hidden>{lead?.[1] ?? "🧠"}</span>
        <span className="session-live-label">{label}</span>
        <span className="muted">
          {p?.step ? `단계 ${p.step}/${p.max_steps ?? "?"} · ` : ""}도구 {p?.tools ?? view.tools.length}회
        </span>
      </div>
      {showTools && lastTool && (
        <div className="session-live-last">
          <span className="session-live-cap">방금 확인</span>
          <ToolChip msg={lastTool} />
        </div>
      )}
      {lastNote && <p className="session-live-note clamp">💭 {lastNote.content}</p>}
    </div>
  );
}

function ToolsSection({ view }: { view: SessionView }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const groups = groupTools(view.tools);
  return (
    <div className="session-sec">
      <button
        type="button"
        className="session-sec-title session-toggle"
        onClick={() => setOpen((x) => !x)}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
      >
        <span className="session-icon" aria-hidden>
          🔎
        </span>
        확인한 정보
        <span className="session-sum">
          {groups.map((g) => (
            <span key={g.key} className="session-cat-chip">
              {g.icon} {g.label} <b>{g.msgs.length}</b>
            </span>
          ))}
        </span>
        <span className="session-caret">{open ? "접기 ▴" : "펼치기 ▾"}</span>
      </button>
      {open && (
        <div className="session-sec-body session-tools" id={id}>
          {groups.map((g) => (
            <div key={g.key} className="session-cat" data-testid={`session-cat-${g.key}`}>
              <div className="session-cat-head">
                <b>
                  {g.icon} {g.label}
                </b>
                <span className="muted">{g.msgs.length}회</span>
                {g.sources.map(([s, n]) => (
                  <span key={s} className={`session-src ${SOURCE_CLS[s] ?? ""}`}>
                    {s} {n}
                  </span>
                ))}
                {g.failed > 0 && <span className="session-src fail">실패 {g.failed}</span>}
              </div>
              {g.msgs.map((m) => (
                <ToolChip key={m.id} msg={m} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NotesSection({ notes }: { notes: ChatMsg[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="session-sec">
      <button
        type="button"
        className="session-sec-title session-toggle"
        onClick={() => setOpen((x) => !x)}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
      >
        <span className="session-icon" aria-hidden>
          💭
        </span>
        매니저 메모
        <span className="muted">{notes.length}개</span>
        <span className="session-caret">{open ? "접기 ▴" : "펼치기 ▾"}</span>
      </button>
      {open && (
        <ul className="session-sec-body session-notes" id={id}>
          {notes.map((n) => (
            <li key={n.id} className={n.role}>
              <time>{fmtTime(n.ts)}</time>
              <span>{n.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
