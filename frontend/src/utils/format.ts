const won = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

export const fmtWon = (v: number | null | undefined) => (v == null ? "—" : `${won.format(v)}원`);
export const fmtNum = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(v);
export const fmtPct = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
export const signClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "up" : "down";

// ---------- 주요 지표 (대시보드) ----------
const dec = (min: number, max = min) =>
  new Intl.NumberFormat("ko-KR", { minimumFractionDigits: min, maximumFractionDigits: max });
const dec2 = dec(2);
const dec3 = dec(3);
const decFx = dec(1, 2);

/** 단위별 값: pt 5,648.79 · $ $686.13 · 원 1,344.5원 · % 4.002% */
export function fmtIndicator(v: number | null | undefined, unit: string): string {
  if (v == null || !Number.isFinite(v)) return "—";
  switch (unit) {
    case "$":
      return `${v < 0 ? "-" : ""}$${dec2.format(Math.abs(v))}`;
    case "원":
      return `${decFx.format(v)}원`;
    case "%":
      return `${v.toFixed(3)}%`;
    default:
      return dec2.format(v);
  }
}

type IndicatorMove = {
  kind: string;
  change: number | null;
  change_pct: number | null;
  direction: "up" | "down" | null;
};

/** 전일 대비 방향: 변화량 → 등락률 → 서버가 준 방향 순으로 본다 */
export function indicatorDirection(it: IndicatorMove): "up" | "down" | null {
  const v = it.change ?? it.change_pct;
  if (v != null && Number.isFinite(v)) return v > 0 ? "up" : v < 0 ? "down" : null;
  return it.direction ?? null;
}

const ARROW = { up: "▲", down: "▼" } as const;

/** 전일 대비 한 줄: 지수·ETF ▲ 12.34 (+0.45%) · 채권 ▲ 0.012%p · 환율은 화살표만. 보일 게 없으면 "" */
export function fmtIndicatorChange(it: IndicatorMove): string {
  const dir = indicatorDirection(it);
  if (it.kind === "fx") return dir ? ARROW[dir] : "";
  const arrow = dir ? `${ARROW[dir]} ` : "";
  if (it.kind === "bond") return it.change == null ? "" : `${arrow}${dec3.format(Math.abs(it.change))}%p`;
  const pct = it.change_pct == null ? "" : fmtPct(it.change_pct);
  if (it.change == null) return pct ? `${arrow}${pct}` : "";
  const abs = dec2.format(Math.abs(it.change));
  return pct ? `${arrow}${abs} (${pct})` : `${arrow}${abs}`;
}

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
