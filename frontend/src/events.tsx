import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { get, getToken } from "./api/client";
import type { AuditEvent, ChatMsg, Role } from "./api/types";

interface Typing {
  role: Role;
  label: string;
}

interface EventsState {
  messages: ChatMsg[];
  audit: AuditEvent[];
  typing: Typing | null;
  connected: boolean;
  simTime: string | null;
}

const Ctx = createContext<EventsState>({
  messages: [],
  audit: [],
  typing: null,
  connected: false,
  simTime: null,
});

export const useEvents = () => useContext(Ctx);

type WsMsg =
  | { channel: "chat"; data: ChatMsg }
  | { channel: "audit"; data: AuditEvent }
  | { channel: "typing"; data: { role: Role; active: boolean; label: string } }
  | { channel: "state"; data: Record<string, unknown> }
  | { channel: "hello" | "pong"; data: unknown };

export function EventsProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [typing, setTyping] = useState<Typing | null>(null);
  const [connected, setConnected] = useState(false);
  const [simTime, setSimTime] = useState<string | null>(null);
  const invalidateTimer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    get<ChatMsg[]>("/api/chat?limit=200")
      .then((m) => alive && setMessages((cur) => merge(m, cur)))
      .catch(() => {});
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
        } else if (msg.channel === "state") {
          if (typeof msg.data.sim_time === "string") setSimTime(msg.data.sim_time);
          if (Object.keys(msg.data).some((k) => k !== "sim_time")) refresh();
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearInterval(ping);
      ws?.close();
    };
  }, [qc]);

  const value = useMemo(
    () => ({ messages, audit, typing, connected, simTime }),
    [messages, audit, typing, connected, simTime],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function merge(a: ChatMsg[], b: ChatMsg[]): ChatMsg[] {
  const byId = new Map<number, ChatMsg>();
  for (const m of [...a, ...b]) byId.set(m.id, m);
  return [...byId.values()].sort((x, y) => x.id - y.id).slice(-800);
}
