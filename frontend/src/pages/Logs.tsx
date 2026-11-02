import { useMemo, useState } from "react";
import { useEvents } from "../events";
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
  const { audit } = useEvents();
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
      <table className="table">
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
            <tr key={e.id}>
              <td>{fmtDateTime(e.ts)}</td>
              <td>
                <span className={`pill t-${e.type.toLowerCase()}`}>{e.type}</span>
              </td>
              <td>{e.symbol ?? ""}</td>
              <td className="wrap">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
