import type { MarketCode, ScheduleInfo } from "../api/types";

/** 시장별 판단 주기 API (국장 /api/schedule · 미장 /api/us/schedule — 미장이 꺼져 있으면 404) */
export const SCHEDULE_API: Record<MarketCode, string> = {
  KR: "/api/schedule",
  US: "/api/us/schedule",
};

export const MARKET_TABS: { key: MarketCode; label: string }[] = [
  { key: "KR", label: "국장" },
  { key: "US", label: "미장" },
];

/** 하루 판단 세션 상한 (서버와 같은 기준) */
export const MAX_SESSIONS = 96;
/** 시작·끝 시각 단위(분) */
export const TIME_STEP = 5;
/** 장 시작 몇 분 전부터 주문을 받는지 (그 전은 '장 시간 밖') */
export const PRE_OPEN_MINUTES = 30;

const pad = (n: number) => String(n).padStart(2, "0");

/** "HH:MM" → 자정부터 분. 형식이 틀리면 NaN */
export function toMinutes(hhmm: string): number {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return NaN;
  const h = Number(m[1]);
  const mm = Number(m[2]);
  if (h > 23 || mm > 59) return NaN;
  return h * 60 + mm;
}

/** 자정부터 분 → "HH:MM" (하루를 넘으면 한 바퀴 돌린다) */
export const fromMinutes = (n: number): string => {
  const v = ((n % 1440) + 1440) % 1440;
  return `${pad(Math.floor(v / 60))}:${pad(v % 60)}`;
};

/** 시작·끝 고르기용: 00:00 ~ 23:55 (5분 단위) */
export const TIME_OPTIONS: string[] = Array.from({ length: 1440 / TIME_STEP }, (_, i) =>
  fromMinutes(i * TIME_STEP),
);

/**
 * 판단 시각 미리 보기 — 서버와 같은 규칙.
 * 자정부터 주기 단위로 센 '딱 떨어지는 시각' 중 [start, end] 안에 드는 것만.
 * 예) ("08:00","17:00",60) → 08:00, 09:00, …, 17:00 (10개) · ("08:10","09:00",15) → 08:15, 08:30, 08:45, 09:00
 */
export function scheduleTimes(start: string, end: string, interval: number): string[] {
  const s = toMinutes(start);
  const e = toMinutes(end);
  if (!Number.isFinite(s) || !Number.isFinite(e) || !(interval > 0) || e < s) return [];
  const out: string[] = [];
  for (let t = Math.ceil(s / interval) * interval; t <= e; t += interval) out.push(fromMinutes(t));
  return out;
}

/** 장외 세션 판단은 세션 끝 몇 분 전까지 (서버 EXT_CUTOFF 와 같다) */
export const EXT_CUTOFF_MINUTES = 10;

/**
 * 장외 세션 [start, end) 안의 판단 시각 — 서버(scheduler.ext_session_times)와 같은 규칙.
 * 자정부터 주기로 딱 떨어지는 시각, 세션 시작 ~ 끝 cutoff 분 전. 끝이 시작보다 이르면 자정을 넘는 세션(미장 데이마켓).
 */
export function extSessionTimes(
  start: string,
  end: string,
  interval: number,
  cutoff: number = EXT_CUTOFF_MINUTES,
): string[] {
  const s = toMinutes(start);
  let e = toMinutes(end);
  if (!Number.isFinite(s) || !Number.isFinite(e) || !(interval > 0)) return [];
  if (e <= s) e += 1440;
  const out: string[] = [];
  for (let t = Math.ceil(s / interval) * interval; t <= e - cutoff; t += interval) out.push(fromMinutes(t));
  return out;
}

/** 시각이 [start, end) 세션 안인지 (자정을 넘는 세션도) */
export function inWindow(time: string, start: string, end: string): boolean {
  const t = toMinutes(time);
  const s = toMinutes(start);
  const e = toMinutes(end);
  if (![t, s, e].every(Number.isFinite)) return false;
  return e > s ? t >= s && t < e : t >= s || t < e;
}

/** 주문을 낼 수 없는 시각: 장 시작 30분 전보다 이르거나 주문 마감 시각 이후(마감 시각 포함 — 서버와 같은 규칙, 시장 시각 기준) */
export function outsideMarketTimes(times: string[], hours: ScheduleInfo["market_hours"]): string[] {
  const from = toMinutes(hours.open) - PRE_OPEN_MINUTES;
  const cutoff = toMinutes(hours.order_cutoff);
  return times.filter((t) => {
    const m = toMinutes(t);
    return m < from || m >= cutoff;
  });
}

/**
 * 판단 시각(한국 시각 "HH:MM")이 장 시작 전인지. 장 시작 직전 12시간 안이면 장전으로 본다
 * (미장처럼 자정을 넘겨도: 개장 23:30 → 23:05 는 장전, 02:30 은 장중). 개장 시각 자체는 장중
 */
export function isPreOpen(time: string, openKst: string): boolean {
  const t = toMinutes(time);
  const o = toMinutes(openKst);
  if (!Number.isFinite(t) || !Number.isFinite(o)) return false;
  return (t - o + 1440) % 1440 > 720;
}

/** 주기 이름: 5분 · 15분 · 1시간 · 1시간 30분 · 2시간 */
export function intervalLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}분`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}시간 ${m}분` : `${h}시간`;
}

/** 저장 전에 확인 — 문제가 없으면 null (서버도 같은 것을 다시 검사한다) */
export function validateSchedule(
  start: string,
  end: string,
  interval: number,
  intervals: number[],
  maxSessions: number = MAX_SESSIONS,
): string | null {
  const s = toMinutes(start);
  const e = toMinutes(end);
  if (!Number.isFinite(s) || !Number.isFinite(e)) return "시작·끝 시각을 HH:MM 으로 골라 주세요";
  if (s % TIME_STEP || e % TIME_STEP) return `시작·끝 시각은 ${TIME_STEP}분 단위여야 해요`;
  if (e <= s) return "끝 시각은 시작 시각보다 늦어야 해요";
  if (intervals.length > 0 && !intervals.includes(interval)) return "고를 수 없는 주기예요";
  if (!(interval > 0)) return "주기를 골라 주세요";
  const n = scheduleTimes(start, end, interval).length;
  if (n === 0) return "이 범위에는 판단 시각이 하나도 없어요 — 시작·끝이나 주기를 바꿔 주세요";
  if (n > maxSessions) return `판단 세션이 하루 ${n}회로 너무 많아요 (최대 ${maxSessions}회)`;
  return null;
}

/** 그 시간대의 UTC 오프셋(분). 예: Asia/Seoul → 540, America/New_York → -240(서머타임) / -300 */
export function tzOffsetMinutes(timeZone: string, at: Date = new Date()): number {
  const t = new Date(Math.floor(at.getTime() / 60_000) * 60_000);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(t);
  const v = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(v("year"), v("month") - 1, v("day"), v("hour") % 24, v("minute"));
  return Math.round((asUtc - t.getTime()) / 60_000);
}

/**
 * 시장 시각 → 한국 시각으로 옮길 때 더할 분 (0 ~ 1439).
 * 서버가 준 times / times_kst 짝이 있으면 그걸 쓴다(서머타임까지 서버 기준), 없으면 브라우저로 계산.
 */
export function kstShift(info: Pick<ScheduleInfo, "timezone" | "times" | "times_kst">, at?: Date): number {
  if (info.times.length > 0 && info.times_kst.length === info.times.length) {
    const d = toMinutes(info.times_kst[0]) - toMinutes(info.times[0]);
    if (Number.isFinite(d)) return ((d % 1440) + 1440) % 1440;
  }
  if (info.timezone === "Asia/Seoul") return 0;
  try {
    const d = tzOffsetMinutes("Asia/Seoul", at) - tzOffsetMinutes(info.timezone, at);
    return ((d % 1440) + 1440) % 1440;
  } catch {
    return 0;
  }
}

/** 시장 시각 "HH:MM" → 한국 시각 { time, nextDay } */
export function toKst(hhmm: string, shift: number): { time: string; nextDay: boolean } {
  const m = toMinutes(hhmm) + shift;
  return { time: fromMinutes(m), nextDay: m >= 1440 };
}

/** "22:30" · 다음 날이면 "05:00(+1일)" */
export const kstText = (hhmm: string, shift: number): string => {
  const k = toKst(hhmm, shift);
  return k.nextDay ? `${k.time}(+1일)` : k.time;
};
