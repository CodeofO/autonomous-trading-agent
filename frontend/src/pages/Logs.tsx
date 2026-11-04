import { memo, useMemo, useState } from "react";
import type { AuditEvent } from "../api/types";
import { SkeletonLines } from "../components/Skeleton";
import { useAudit, useAuditReady } from "../live/hooks";
import { fmtDateTime } from "../utils/format";

const TYPES = [
  "ALL",
  "SIGNAL",
  "ORDER",
  "FILL",
  "RISK_BLOCK",
  "KILL_SWITCH",
  "AGENT_RUN",
  "REPORT",
  "CONFIG",
  "ERROR",
];

export function Logs() {
  // 감사 로그는 이 화면에서만 받아 온다 (다른 화면에서는 받지 않는다)
  const audit = useAudit();
  const ready = useAuditReady();
  const [type, setType] = useState("ALL");
  const rows = useMemo(() => (type === "ALL" ? audit : audit.filter((a) => a.type === type)), [audit, type]);
  return (
    <section className="card">
      <div className="card-head">
        <h2>📜 이벤트 로그 (감사 기록)</h2>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="이벤트 유형">
          {TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </div>
      <p className="muted">
        모든 신호·주문·체결·리스크 차단·설정 변경이 DB 에 남고, 여기에 실시간으로 흘러요.
      </p>
      {rows.length === 0 &&
        (!ready ? (
          <SkeletonLines lines={8} label="이벤트 로그 불러오는 중" />
        ) : (
          <div className="empty-action">
            <p className="muted empty">
              {type === "ALL" ? "아직 기록이 없어요." : `${type} 기록이 아직 없어요.`}
            </p>
            {type !== "ALL" && (
              <button type="button" className="btn tiny ghost" onClick={() => setType("ALL")}>
                모든 유형 보기
              </button>
            )}
          </div>
        ))}
      {rows.length > 0 && (
        <table className="table logs-table">
          <thead>
            <tr>
              <th>시각</th>
              <th>유형</th>
              <th>종목</th>
              <th>내용</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <LogRow key={e.id} e={e} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** 한 줄 — 새 기록이 와도 이미 있던 줄은 다시 그리지 않는다 (시각 형식도 줄마다 한 번만) */
const LogRow = memo(function LogRow({ e }: { e: AuditEvent }) {
  return (
    <tr>
      <td>{fmtDateTime(e.ts)}</td>
      <td>
        <span className={`pill t-${e.type.toLowerCase()}`}>{e.type}</span>
      </td>
      <td>{e.symbol ?? ""}</td>
      <td className="wrap">{e.message}</td>
    </tr>
  );
});
