import type { ExtendedSession, MarketCode } from "../api/types";

/** 한국 시각 HH:MM (ISO 문자열에서) */
function kstHm(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Seoul",
  });
}

/** 세션 시각 한 줄: 한국 시각 (미장은 현지 시각도) */
export function sessionTimeText(s: ExtendedSession, market: MarketCode): string {
  if (!s.next) return "시각 정보 없음";
  const kst = `${kstHm(s.next.start)}~${kstHm(s.next.end)}`;
  return market === "US" ? `한국 ${kst} · 뉴욕 ${s.next.start_local}~${s.next.end_local}` : `${kst}`;
}
