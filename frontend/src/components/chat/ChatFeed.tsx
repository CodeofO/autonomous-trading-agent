import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { errorText, post } from "../../api/client";
import type { ChatMsg, Role } from "../../api/types";
import { ChatMessage, roleMeta, type Persona } from "./ChatMessage";

interface Props {
  messages: ChatMsg[];
  typing: { role: Role; label: string } | null;
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

export function ChatFeed({ messages, typing, persona, showInput = true, showTools = true, height }: Props) {
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

  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [visible.length, typing, stick]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onScroll = () => setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (t) send.mutate(t);
  };

  return (
    <div className="chat" style={height ? { height } : undefined}>
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
              placeholder={`${persona.name} 매니저에게 물어보기… (메모는 다음 판단에 참고돼요)`}
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
