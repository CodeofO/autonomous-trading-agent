import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get, getToken } from "./api/client";
import type { AgentProgress, AuditEvent, ChatMsg, Role } from "./api/types";

interface Typing {
  role: Role;
  label: string;
}

interface EventsState {
  messages: ChatMsg[];
  audit: AuditEvent[];
  typing: Typing | null;
  progress: AgentProgress | null;
  connected: boolean;
}

const Ctx = createContext<EventsState>({
  messages: [],
  audit: [],
  typing: null,
  progress: null,
  connected: false,
});

export const useEvents = () => useContext(Ctx);

type WsMsg =
  | { channel: "chat"; data: ChatMsg }
  | { channel: "audit"; data: AuditEvent }
  | { channel: "typing"; data: { role: Role; active: boolean; label: string } }
  | { channel: "progress"; data: AgentProgress }
  | { channel: "state"; data: Record<string, unknown> }
  | { channel: "hello" | "pong"; data: unknown };

export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [typing, setTyping] = useState<Typing | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [connected, setConnected] = useState(false);
  const invalidateTimer = useRef<number | null>(null);
  // 진행 이벤트를 받을 때마다 증가 — 그보다 늦게 도착한 GET 응답(옛 상태)은 버린다
  const progressSeq = useRef(0);

  useEffect(() => {
    let alive = true;
    get<AuditEvent[]>("/api/events?limit=200")
      .then((a) => alive && setAudit(a))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let stopped = false;
    let ping: number | undefined;

    const applyProgress = (p: AgentProgress) => {
      setProgress(p);
      setTyping(p.typing ? { role: "analyst", label: p.typing } : null);
    };

    // 새로고침·재접속 시 서버 상태로 맞춘다: 끊긴 동안 놓친 메시지 + 지금 진행 중인 작업
    const sync = () => {
      get<ChatMsg[]>("/api/chat?limit=200")
        .then((m) => !stopped && setMessages((cur) => merge(m, cur)))
        .catch(() => {});
      const seq = progressSeq.current;
      get<AgentProgress>("/api/agent/progress")
        .then((p) => !stopped && seq === progressSeq.current && applyProgress(p))
        .catch(() => {});
    };

    // 잦은 상태 이벤트에 쿼리를 매번 다시 부르지 않도록 묶어서 갱신
    const refresh = () => {
      if (invalidateTimer.current) return;
      invalidateTimer.current = window.setTimeout(() => {
        invalidateTimer.current = null;
        qc.invalidateQueries();
      }, 600);
    };

    const connect = () => {
      if (stopped) return;
      const token = getToken();
      const query = token ? `?token=${encodeURIComponent(token)}` : "";
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/ws/events${query}`);
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        sync();
        ping = window.setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("PING"), 30_000);
      };
      ws.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (!stopped) window.setTimeout(connect, Math.min(10_000, 500 * 2 ** retry++));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data) as WsMsg;
        if (msg.channel === "chat") {
          setMessages((cur) => merge(cur, [msg.data]));
          if (msg.data.role === "analyst" && msg.data.kind !== "tool") setTyping(null);
          if (["fill", "decision", "summary"].includes(msg.data.kind) || msg.data.role !== "analyst")
            refresh();
        } else if (msg.channel === "audit") {
          setAudit((cur) => [msg.data, ...cur].slice(0, 300));
        } else if (msg.channel === "typing") {
          setTyping(msg.data.active ? { role: msg.data.role, label: msg.data.label } : null);
        } else if (msg.channel === "progress") {
          // 자주 오는 이벤트라 쿼리 갱신(invalidate)은 하지 않는다
          progressSeq.current++;
          applyProgress(msg.data);
        } else if (msg.channel === "state") {
          refresh();
        }
      };
    };
    sync();
    connect();
    return () => {
      stopped = true;
      window.clearInterval(ping);
      ws?.close();
    };
  }, [qc]);

  const value = useMemo(
    () => ({ messages, audit, typing, progress, connected }),
    [messages, audit, typing, progress, connected],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function merge(a: ChatMsg[], b: ChatMsg[]): ChatMsg[] {
  const byId = new Map<number, ChatMsg>();
  for (const m of [...a, ...b]) byId.set(m.id, m);
  return [...byId.values()].sort((x, y) => x.id - y.id).slice(-800);
}
