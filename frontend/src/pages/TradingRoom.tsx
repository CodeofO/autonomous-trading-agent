import { memo, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { errorText } from "../api/client";
import type { AgentProgress, AgentRun } from "../api/types";
import { ChatFeed } from "../components/chat/ChatFeed";
import type { Persona } from "../components/chat/ChatMessage";
import { runDuration, runStatus, STATUS_LABEL, TRIGGER_KO } from "../components/chat/sessions";
import { RelTime } from "../components/RelTime";
import { SkeletonLines } from "../components/Skeleton";
import { usePersona, useShowTools, useSystem } from "../hooks";
import { useChat, useProgress } from "../live/hooks";
import { invalidateNames, MARKET_META, useApi } from "../market";
import { toast } from "../ui/toast";

export function TradingRoom() {
  const qc = useQueryClient();
  const api = useApi();
  const { data: sys } = useSystem();
  const [showTools, setShowTools] = useShowTools();
  const [runFilter, setRunFilter] = useState<number | null>(null);
  const runs = useQuery({
    queryKey: api.key("runs"),
    queryFn: () => api.get<AgentRun[]>("/api/agent/runs?limit=20"),
  });
  const run = useMutation({
    mutationFn: () => api.post("/api/agent/run"),
    // 세션 시작: 매니저 상태·세션 목록·시장 목록 (나머지는 세션 이벤트가 알려 준다)
    onSuccess: () => {
      void invalidateNames(qc, ["system", "runs", "overview", "markets"], api.market);
      toast.info(`🧠 ${MARKET_META[api.market].name} 판단 세션을 시작했어요`);
    },
  });
  const persona = usePersona(sys);

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
        <RoomChat persona={persona} showTools={showTools} runs={runs.data} runFilter={runFilter} />
      </section>
      <RunList runs={runs.data} loading={runs.isPending} runFilter={runFilter} onPick={setRunFilter} />
    </div>
  );
}

/** 채팅만 실시간 메시지·진행 상태를 구독한다 (머리줄·세션 목록은 다시 그리지 않는다) */
function RoomChat({
  persona,
  showTools,
  runs,
  runFilter,
}: {
  persona: Persona;
  showTools: boolean;
  runs: AgentRun[] | undefined;
  runFilter: number | null;
}) {
  const { messages, typing, progress, ready } = useChat();
  const shown = useMemo(
    () => (runFilter == null ? messages : messages.filter((m) => m.run_id === runFilter)),
    [messages, runFilter],
  );
  return (
    <ChatFeed
      messages={shown}
      typing={typing}
      progress={progress}
      persona={persona}
      showTools={showTools}
      runs={runs}
      groupSessions
      loading={!ready}
    />
  );
}

/** 오른쪽 판단 세션 목록 — 진행 상태(끊긴 세션 판단)만 구독 */
function RunList({
  runs,
  loading,
  runFilter,
  onPick,
}: {
  runs: AgentRun[] | undefined;
  loading: boolean;
  runFilter: number | null;
  onPick: (id: number | null) => void;
}) {
  const progress = useProgress();
  return (
    <aside className="card runs">
      <div className="runs-head">
        <h3>판단 세션</h3>
        <small className="muted">누르면 그 세션 대화만 보여요</small>
      </div>
      <button className={`run-item ${runFilter == null ? "active" : ""}`} onClick={() => onPick(null)}>
        전체 대화
      </button>
      {loading && <SkeletonLines lines={6} label="판단 세션 불러오는 중" />}
      {!loading && runs?.length === 0 && <p className="muted runs-empty">아직 판단 세션이 없어요.</p>}
      {(runs ?? []).map((r) => (
        <RunItem
          key={r.id}
          r={r}
          active={runFilter === r.id}
          stale={r.status === "RUNNING" && isStale(progress, r.id)}
          onPick={onPick}
        />
      ))}
    </aside>
  );
}

// 서버 기록이 RUNNING 인데 지금 도는 세션이 아니면 중간에 끊긴 세션이다
const isStale = (progress: AgentProgress | null, runId: number) =>
  !!progress && !(progress.active && progress.run_id === runId);

const RunItem = memo(function RunItem({
  r,
  active,
  stale,
  onPick,
}: {
  r: AgentRun;
  active: boolean;
  stale: boolean;
  onPick: (id: number) => void;
}) {
  const st = stale ? "ended" : runStatus(r.status);
  const dur = runDuration(r);
  return (
    <button
      className={`run-item ${active ? "active" : ""}`}
      onClick={() => onPick(r.id)}
      title={`${r.mode} · ${r.model}`}
      data-testid="run-item"
    >
      <div className="run-line">
        <b>세션 #{r.id}</b>
        <span className="muted">{TRIGGER_KO[r.trigger] ?? r.trigger}</span>
        <span className={`pill ${stale ? "" : r.status.toLowerCase()}`}>{STATUS_LABEL[st]}</span>
      </div>
      <small className="muted">
        <RelTime iso={r.started_at} />
        {dur && ` · ${dur}`}
      </small>
      {r.summary && <p className="run-summary">{r.summary}</p>}
      {r.error && (
        <p className="error run-error" title={r.error}>
          ⚠️ {r.error}
        </p>
      )}
    </button>
  );
});
