import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { errorText, post } from "../../api/client";
import type { AgentProgress, ChatMsg, Role } from "../../api/types";
import { ChatMessage, roleMeta, type Persona } from "./ChatMessage";

interface Props {
  messages: ChatMsg[];
  typing: { role: Role; label: string } | null;
  progress?: AgentProgress | null;
  persona: Persona;
  showInput?: boolean;
  showTools?: boolean;
  height?: string;
}

const QUICK = [
  "지금 포트폴리오 어때? 🤔",
  "최근에 왜 그 종목 샀어?",
  "리스크 괜찮아?",
  "다음엔 반도체 좀 더 봐줘",
];

export function ChatFeed({
  messages,
  typing,
  progress,
  persona,
  showInput = true,
  showTools = true,
  height,
}: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true);
  const [text, setText] = useState("");
  const visible = useMemo(
    () => (showTools ? messages : messages.filter((m) => m.kind !== "tool")),
    [messages, showTools],
  );

  const send = useMutation({
    mutationFn: (message: string) => post("/api/chat", { message }),
    onSuccess: () => setText(""),
  });

  const stickRef = useRef(stick);
  stickRef.current = stick;

  // 메시지가 늘거나 바뀌면(도구 결과·결정 카드가 채워지는 경우 포함) 맨 아래에 붙어 있을 때만 따라 내려간다
  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [visible, typing, progress?.active, stick]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onScroll = () => setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
    el.addEventListener("scroll", onScroll, { passive: true });
    // 채팅 창 높이가 바뀌어도(키보드·진행 표시·화면 회전) 아래에 붙은 상태를 유지
    const ro =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            if (stickRef.current) el.scrollTop = el.scrollHeight;
          });
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (t) send.mutate(t);
  };

  return (
    <div className="chat" style={height ? { height } : undefined}>
      {progress?.active && <ActivityPanel progress={progress} />}
      <div className="chat-scroll" ref={box} data-testid="chat-scroll">
        {visible.length === 0 && (
          <div className="chat-empty">
            <div className="big-emoji">{persona.emoji}</div>
            <p>아직 조용하네요. 판단 세션이 시작되면 여기서 실시간으로 생각을 들려드릴게요.</p>
          </div>
        )}
        {visible.map((m, i) => {
          const prev = visible[i - 1];
          const compact =
            !!prev &&
            prev.role === m.role &&
            prev.kind !== "tool" &&
            m.kind !== "tool" &&
            m.role !== "system" &&
            new Date(m.ts).getTime() - new Date(prev.ts).getTime() < 120_000;
          return <ChatMessage key={m.id} msg={m} persona={persona} compact={compact} />;
        })}
        {typing && <TypingIndicator role={typing.role} label={typing.label} persona={persona} />}
      </div>
      {!stick && (
        <button className="jump" onClick={() => setStick(true)}>
          새 메시지 ↓
        </button>
      )}
      {showInput && (
        <div className="chat-compose">
          <div className="quick">
            {QUICK.map((q) => (
              <button key={q} type="button" onClick={() => send.mutate(q)} disabled={send.isPending}>
                {q}
              </button>
            ))}
          </div>
          <form onSubmit={submit}>
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                progress?.replying
                  ? "답장 쓰는 중이에요 — 지금 보내면 이어서 함께 답해요"
                  : `${persona.name} 매니저에게 물어보기… (메모는 다음 판단에 참고돼요)`
              }
              maxLength={1000}
              aria-label="메시지 입력"
            />
            <button className="btn primary" disabled={!text.trim() || send.isPending}>
              보내기
            </button>
          </form>
          {send.isError && <p className="error">{errorText(send.error)}</p>}
        </div>
      )}
    </div>
  );
}

const PHASE_ICON: Record<string, string> = { start: "☕", thinking: "🧠", tool: "🔎", executing: "🛡️" };
// 도구 라벨은 "📰 뉴스 검색"처럼 이모지로 시작한다 — 아이콘 자리로 옮기고 본문에서는 뺀다
const LEAD_EMOJI = /^(\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*)\s*/u;

function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function fmtElapsed(ms: number): string | null {
  if (!Number.isFinite(ms) || ms < 0 || ms > 6 * 3600_000) return null;
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** 판단 세션이 도는 동안 채팅 위에 고정되는 진행 표시 (서버 상태 그대로 — 새로고침해도 복원) */
function ActivityPanel({ progress }: { progress: AgentProgress }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const phase = progress.phase ?? "thinking";
  const raw = progress.label ?? "";
  const lead = raw.match(LEAD_EMOJI);
  const icon = (phase === "tool" && lead?.[1]) || PHASE_ICON[phase] || "🧠";
  const label = (lead ? raw.slice(lead[0].length) : raw) || "작업 중…";
  const elapsed = progress.started_at ? fmtElapsed(now - new Date(progress.started_at).getTime()) : null;
  const sources = Object.entries(progress.sources ?? {}).filter(([, n]) => n > 0);

  return (
    <div className={`activity ${phase}`} data-testid="activity">
      <div className="activity-meta">
        <span className="live-dot" aria-hidden />
        <b>세션 #{progress.run_id ?? "—"}</b>
        {!!progress.step && (
          <span>
            단계 {progress.step}/{progress.max_steps ?? "?"}
          </span>
        )}
        {elapsed && <span className="activity-time">{elapsed}</span>}
        <span>도구 {progress.tools ?? 0}회</span>
        {!!progress.tokens && <span>{fmtTokens(progress.tokens)} 토큰</span>}
      </div>
      <div className="activity-now" role="status" aria-live="polite">
        <span className="activity-icon" aria-hidden>
          {icon}
        </span>
        <span className="activity-label">{label}</span>
      </div>
      {sources.length > 0 && (
        <div className="activity-sources">
          {sources.map(([name, n]) => (
            <span key={name} className={`source-chip ${name === progress.source ? "on" : ""}`}>
              {name} <b>{n}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function TypingIndicator({ role, label, persona }: { role: Role; label: string; persona: Persona }) {
  const meta = roleMeta(role, persona);
  return (
    <div className={`chat-row ${meta.cls}`} data-testid="typing">
      <div className="avatar" aria-hidden>
        {meta.avatar}
      </div>
      <div className="chat-col">
        <div className="bubble typing">
          <span className="dots">
            <i />
            <i />
            <i />
          </span>
          <span className="typing-label">{label || "입력 중…"}</span>
        </div>
      </div>
    </div>
  );
}
