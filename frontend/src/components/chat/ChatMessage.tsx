import { useState } from "react";
import { Link } from "react-router-dom";
import type { ChatMsg, Role } from "../../api/types";
import { ACTION_KO, fmtNum, fmtTime, fmtWon } from "../../utils/format";

export interface Persona {
  name: string;
  emoji: string;
}

export function roleMeta(role: Role, persona: Persona) {
  switch (role) {
    case "analyst":
      return { name: `${persona.name} 매니저`, avatar: persona.emoji, cls: "analyst" };
    case "risk":
      return { name: "리스크봇", avatar: "🛡️", cls: "risk" };
    case "broker":
      return { name: "체결봇", avatar: "💸", cls: "broker" };
    case "user":
      return { name: "나", avatar: "🙋", cls: "user" };
    default:
      return { name: "시스템", avatar: "🔔", cls: "system" };
  }
}

interface Props {
  msg: ChatMsg;
  persona: Persona;
  compact?: boolean; // 같은 화자의 연속 메시지면 아바타 생략
}

export function ChatMessage({ msg, persona, compact = false }: Props) {
  const meta = roleMeta(msg.role, persona);

  if (msg.role === "system" && msg.kind !== "summary") {
    return (
      <div className="chat-divider" data-testid="chat-system">
        <span>{msg.content}</span>
        <time>{fmtTime(msg.ts)}</time>
      </div>
    );
  }
  if (msg.kind === "tool") return <ToolChip msg={msg} />;

  return (
    <div className={`chat-row ${meta.cls} ${compact ? "compact" : ""}`} data-testid={`chat-${msg.kind}`}>
      {msg.role !== "user" && (
        <div className="avatar" aria-hidden>
          {compact ? "" : meta.avatar}
        </div>
      )}
      <div className="chat-col">
        {!compact && (
          <div className="chat-meta">
            <b>{meta.name}</b>
            <time>{fmtTime(msg.ts)}</time>
          </div>
        )}
        {msg.kind === "decision" ? (
          <DecisionCard msg={msg} />
        ) : msg.kind === "summary" ? (
          <div className="bubble summary">
            <span className="summary-tag">{msg.role === "system" ? "📊 리포트" : "🏁 세션 정리"}</span>
            <p>{msg.content}</p>
            <SummaryCounts payload={msg.payload} />
          </div>
        ) : (
          <div className={`bubble ${msg.kind}`}>{msg.content}</div>
        )}
      </div>
    </div>
  );
}

function SummaryCounts({ payload }: { payload: Record<string, unknown> }) {
  const counts = payload.counts as Record<string, number> | undefined;
  if (!counts) return null;
  return (
    <div className="counts">
      <span className="pill buy">매수 {counts.BUY ?? 0}</span>
      <span className="pill sell">매도 {counts.SELL ?? 0}</span>
      <span className="pill hold">유지 {counts.HOLD ?? 0}</span>
    </div>
  );
}

function ToolChip({ msg }: { msg: ChatMsg }) {
  const [open, setOpen] = useState(false);
  const preview = (msg.payload.preview ?? {}) as Record<string, unknown>;
  const hasPreview = Object.keys(preview).length > 0;
  return (
    <div className="tool-row" data-testid="chat-tool">
      <button className="tool-chip" onClick={() => setOpen((v) => !v)} disabled={!hasPreview}>
        <span>{msg.content}</span>
        {hasPreview && <span className="caret">{open ? "▴" : "▾"}</span>}
      </button>
      {open && <pre className="tool-preview">{JSON.stringify(preview, null, 1)}</pre>}
    </div>
  );
}

export function DecisionCard({ msg }: { msg: ChatMsg }) {
  const p = msg.payload as {
    symbol?: string;
    name?: string;
    action?: "BUY" | "SELL" | "HOLD";
    quantity?: number;
    target_weight?: number | null;
    confidence?: number;
    rationale?: string;
    evidence?: string[];
    price?: number;
  };
  const action = p.action ?? "HOLD";
  const conf = Math.round((p.confidence ?? 0) * 100);
  return (
    <div className={`decision-card ${action.toLowerCase()}`} data-testid="decision-card">
      <div className="decision-head">
        <span className={`action-badge ${action.toLowerCase()}`}>{ACTION_KO[action]}</span>
        <Link to={`/stock/${p.symbol}`} className="decision-name">
          {p.name || p.symbol} <small>{p.symbol}</small>
        </Link>
        {p.price ? <span className="muted">{fmtWon(p.price)}</span> : null}
      </div>
      {action !== "HOLD" && (
        <div className="decision-size">
          {p.quantity ? `${fmtNum(p.quantity)}주` : ""}
          {p.target_weight != null ? ` · 목표 비중 ${(p.target_weight * 100).toFixed(0)}%` : ""}
        </div>
      )}
      <div className="confidence" aria-label={`확신도 ${conf}%`}>
        <div className="bar">
          <div style={{ width: `${conf}%` }} />
        </div>
        <span>확신도 {conf}%</span>
      </div>
      {p.rationale && <p className="rationale">{p.rationale}</p>}
      {p.evidence && p.evidence.length > 0 && (
        <ul className="evidence">
          {p.evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
