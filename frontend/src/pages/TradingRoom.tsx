import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText, get, post } from "../api/client";
import type { AgentRun } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import { useEvents } from "../events";
import { DEFAULT_PERSONA, useSystem } from "../hooks";
import { fmtDateTime } from "../utils/format";

const TRIGGER_KO: Record<string, string> = { morning: "장전", afternoon: "오후", manual: "수동" };

export function TradingRoom() {
  const qc = useQueryClient();
  const { data: sys } = useSystem();
  const { messages, typing } = useEvents();
  const [showTools, setShowTools] = useState(true);
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => get<AgentRun[]>("/api/agent/runs?limit=20") });
  const run = useMutation({
    mutationFn: () => post("/api/agent/run"),
    onSuccess: () => qc.invalidateQueries(),
  });
  const persona = sys?.persona ?? DEFAULT_PERSONA;
  const shown = useMemo(
    () => (runFilter == null ? messages : messages.filter((m) => m.run_id === runFilter)),
    [messages, runFilter],
  );

  return (
    <div className="room">
      <section className="card room-main">
        <div className="card-head">
          <h2>
            💬 트레이딩 룸 <small className="muted">— {persona.emoji} 매니저 · 🛡️ 리스크봇 · 💸 체결봇</small>
          </h2>
          <div className="btn-row">
            <label className="toggle">
              <input type="checkbox" checked={showTools} onChange={(e) => setShowTools(e.target.checked)} />
              리서치 과정 보기
            </label>
            <button
              className="btn primary"
              onClick={() => run.mutate()}
              disabled={run.isPending || sys?.agent_running || sys?.engine_status !== "RUNNING"}
            >
              🧠 지금 판단 실행
            </button>
          </div>
        </div>
        {run.isError && <p className="error">{errorText(run.error)}</p>}
        <ChatFeed
          messages={shown}
          typing={typing}
          persona={persona}
          showTools={showTools}
          height="calc(100vh - 260px)"
        />
      </section>
      <aside className="card runs">
        <h3>판단 세션</h3>
        <button
          className={`run-item ${runFilter == null ? "active" : ""}`}
          onClick={() => setRunFilter(null)}
        >
          전체 대화
        </button>
        {(runs.data ?? []).map((r) => (
          <button
            key={r.id}
            className={`run-item ${runFilter === r.id ? "active" : ""}`}
            onClick={() => setRunFilter(r.id)}
          >
            <div>
              <b>#{r.id}</b> {TRIGGER_KO[r.trigger] ?? r.trigger}
              <span className={`pill ${r.status.toLowerCase()}`}>{r.status}</span>
            </div>
            <small className="muted">
              {fmtDateTime(r.started_at)} · {r.mode}
            </small>
            {r.summary && <p>{r.summary}</p>}
            {r.error && <p className="error">{r.error}</p>}
          </button>
        ))}
      </aside>
    </div>
  );
}
