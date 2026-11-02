import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { ChatMsg, Role } from "../../api/types";
import { ACTION_KO, fmtNum, fmtTime, fmtWon } from "../../utils/format";
import "./chat-activity.css";

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

// 도구 인자·결과 키의 한글 이름 (모르는 키는 그대로 보여준다)
const ARG_KO: Record<string, string> = {
  symbol: "종목",
  query: "검색어",
  days: "기간(일)",
  limit: "개수",
  sort_by: "정렬",
  market: "시장",
};
const RESULT_KO: Record<string, string> = {
  error: "오류",
  titles: "제목",
  count: "건수",
  close: "종가",
  realtime_price: "현재가",
  returns_pct: "수익률(%)",
  rsi14: "RSI(14)",
  ma: "이동평균",
  period: "기간",
  revenue: "매출",
  op_margin_pct: "영업이익률(%)",
  per: "PER",
  pbr: "PBR",
  roe_pct: "ROE(%)",
  top: "후보",
  as_of: "기준",
  items: "공시",
  equity: "평가금",
  cash: "현금",
  positions: "보유 종목",
  realized_pnl_total: "실현손익",
  fill_count: "체결",
  by_stock: "종목별",
  warnings: "유의사항",
  trading_suspended: "거래정지",
  spread_pct: "스프레드(%)",
  bid_ask_volume_ratio: "호가 잔량비",
  top_sectors_1d: "강한 섹터",
  usd_krw: "원/달러",
  sector: "섹터",
  stocks: "종목",
  score: "점수",
  evidence: "근거",
};
const SOURCE_CLS: Record<string, string> = {
  토스증권: "toss",
  DART: "dart",
  NAVER: "naver",
  모의계좌: "account",
  "토스 실계좌": "account",
};

const fmtMs = (ms: number) => (ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`);

const entriesOf = (o: unknown): [string, unknown][] =>
  o && typeof o === "object" && !Array.isArray(o)
    ? Object.entries(o).filter(([k]) => !k.startsWith("_"))
    : [];

function inline(v: unknown): string {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return fmtNum(v, 2);
  if (typeof v === "boolean") return v ? "예" : "아니오";
  if (Array.isArray(v)) return v.length ? v.map(inline).join(", ") : "없음";
  if (typeof v === "object") {
    return (
      entriesOf(v)
        .map(([k, x]) => `${RESULT_KO[k] ?? k}: ${inline(x)}`)
        .join(" · ") || "—"
    );
  }
  return String(v);
}

function Value({ v }: { v: unknown }) {
  // 뉴스 제목처럼 긴 항목이 여럿이면 한 줄씩
  const long =
    Array.isArray(v) && v.length > 1 && v.some((x) => typeof x === "object" || String(x).length > 20);
  if (!long) return <>{inline(v)}</>;
  return (
    <ul className="tool-list">
      {v.map((x, i) => (
        <li key={i}>{inline(x)}</li>
      ))}
    </ul>
  );
}

function KeyValues({
  title,
  rows,
  labels,
}: {
  title: string;
  rows: [string, unknown][];
  labels: Record<string, string>;
}) {
  return (
    <div className="tool-sec">
      <div className="tool-sec-title">{title}</div>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{labels[k] ?? k}</dt>
            <dd>
              <Value v={v} />
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ToolChip({ msg }: { msg: ChatMsg }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const p = msg.payload as {
    tool?: string;
    args?: Record<string, unknown>;
    symbol?: string;
    source?: string;
    ms?: number;
    step?: number | null;
    ok?: boolean;
    line?: string;
    preview?: Record<string, unknown>;
  };
  // 예전 메시지(규칙 모드 포함)는 args 없이 symbol 만 있다
  const args = entriesOf(p.args ?? (p.symbol ? { symbol: p.symbol } : {}));
  const results = entriesOf(p.preview);
  const error = p.preview?.error;
  const failed = p.ok === false || error != null;
  const line = p.line ?? (error != null ? `실패: ${String(error)}` : "");
  const hasDetails = args.length > 0 || results.length > 0;
  return (
    <div className={`tool-row ${failed ? "fail" : ""}`} data-testid="chat-tool">
      <button
        type="button"
        className="tool-chip"
        onClick={() => setOpen((v) => !v)}
        disabled={!hasDetails}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
      >
        <span className="tool-label">{msg.content}</span>
        {p.source && <span className={`tool-src ${SOURCE_CLS[p.source] ?? ""}`}>{p.source}</span>}
        {line && <span className="tool-line">{line}</span>}
        {typeof p.ms === "number" && <span className="tool-ms">{fmtMs(p.ms)}</span>}
        {hasDetails && (
          <span className="caret" aria-hidden>
            {open ? "▴" : "▾"}
          </span>
        )}
      </button>
      {open && (
        <div className="tool-detail" id={panelId}>
          {args.length > 0 && <KeyValues title="요청" rows={args} labels={ARG_KO} />}
          {results.length > 0 && <KeyValues title="결과" rows={results} labels={RESULT_KO} />}
          {p.tool && (
            <div className="tool-tech">
              <code>{p.tool}</code>
              {typeof p.step === "number" && ` · ${p.step}단계`}
            </div>
          )}
        </div>
      )}
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
