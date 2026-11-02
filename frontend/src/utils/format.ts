const won = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

export const fmtWon = (v: number | null | undefined) => (v == null ? "—" : `${won.format(v)}원`);
export const fmtNum = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(v);
export const fmtPct = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
export const signClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "up" : "down";

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" });
}

export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

export const ACTION_KO: Record<string, string> = { BUY: "매수", SELL: "매도", HOLD: "유지" };
export const BASIS_KO: Record<string, string> = { OPEN: "시가", CLOSE: "종가", NOW: "현재가" };

export const STATUS_KO: Record<string, string> = {
  PENDING: "대기",
  SUBMITTED: "접수",
  PARTIALLY_FILLED: "부분체결",
  FILLED: "체결",
  CANCELED: "취소",
  REJECTED: "거부",
};
