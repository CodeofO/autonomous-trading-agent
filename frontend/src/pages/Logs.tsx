import { memo, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { AuditEvent } from "../api/types";
import { SkeletonLines } from "../components/Skeleton";
import { useAudit, useAuditReady } from "../live/hooks";
import { fmtDateTime } from "../utils/format";

/** 기록 유형 → 화면 이름. 묶음(GROUPS)으로 걸러 본다 — 내부 코드는 화면에 내지 않는다 */
export const TYPE_KO: Record<string, string> = {
  SIGNAL: "결정",
  AGENT_RUN: "판단",
  AGENT_PAYLOAD: "판단 원본",
  ORDER: "주문",
  SUBMITTED: "접수",
  FILLED: "체결 완료",
  PARTIALLY_FILLED: "부분 체결",
  CANCELED: "취소",
  REJECTED: "거부",
  EXPIRED: "만료",
  FILL: "체결",
  RISK_BLOCK: "리스크 차단",
  KILL_SWITCH: "킬 스위치",
  ENGINE: "엔진",
  REPORT: "리포트",
  NEWS: "뉴스",
  CONFIG: "설정 변경",
  SYSTEM: "시스템",
  ACCOUNT_RESET: "계좌 초기화",
  ERROR: "오류",
};
const GROUPS: [string, string, string[]][] = [
  ["ALL", "전체", []],
  [
    "TRADE",
    "주문·체결",
    ["ORDER", "SUBMITTED", "FILLED", "PARTIALLY_FILLED", "CANCELED", "REJECTED", "EXPIRED", "FILL"],
  ],
  ["DECISION", "판단·결정", ["SIGNAL", "AGENT_RUN", "AGENT_PAYLOAD", "REPORT"]],
  ["RISK", "리스크·정지", ["RISK_BLOCK", "KILL_SWITCH", "ENGINE"]],
  ["CONFIG", "설정·시스템", ["CONFIG", "SYSTEM", "ACCOUNT_RESET", "NEWS"]],
  ["ERROR", "오류", ["ERROR"]],
];
const WORDS: [RegExp, string][] = [
  [/\bBUY\b/g, "매수"],
  [/\bSELL\b/g, "매도"],
  [/\bHOLD\b/g, "유지"],
  [/\bPARTIALLY_FILLED\b/g, "부분 체결"],
  [/\bFILLED\b/g, "체결"],
  [/\bSUBMITTED\b/g, "접수"],
  [/\bCANCELED\b/g, "취소"],
  [/\bREJECTED\b/g, "거부"],
  [/\bEXPIRED\b/g, "만료"],
  [/\bDONE\b/g, "완료"],
  [/\bFAILED\b/g, "실패"],
  [/\bRUNNING\b/g, "진행 중"],
  [/^세션 #/, "판단 #"],
];
/** 기록 문구 속 내부 코드(BUY·FILLED·DONE…)를 한글로 */
export const logText = (m: string) => WORDS.reduce((t, [re, ko]) => t.replace(re, ko), m);

export function Logs() {
  // 감사 로그는 이 화면에서만 받아 온다 (다른 화면에서는 받지 않는다)
  const audit = useAudit();
  const ready = useAuditReady();
  const [type, setType] = useState("ALL");
  const group = GROUPS.find((g) => g[0] === type) ?? GROUPS[0];
  const rows = useMemo(() => {
    const types = GROUPS.find((g) => g[0] === type)?.[2] ?? [];
    return types.length ? audit.filter((a) => types.includes(a.type)) : audit;
  }, [audit, type]);
  return (
    <section className="card">
      <div className="card-head">
        <h2>활동 기록</h2>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="이벤트 유형">
          {GROUPS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <p className="muted">결정·주문·체결·리스크 차단·설정 변경이 일어난 순서대로 남아요.</p>
      {rows.length === 0 &&
        (!ready ? (
          <SkeletonLines lines={8} label="이벤트 로그 불러오는 중" />
        ) : (
          <div className="empty-action">
            <p className="muted empty">
              {type === "ALL" ? "아직 기록이 없어요." : `${group[1]} 기록이 아직 없어요.`}
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
        <span className={`pill t-${e.type.toLowerCase()}`}>{TYPE_KO[e.type] ?? e.type}</span>
      </td>
      <td>{e.symbol ? <Link to={`/stock/${e.symbol}`}>{e.symbol}</Link> : ""}</td>
      <td className="wrap">{logText(e.message)}</td>
    </tr>
  );
});
