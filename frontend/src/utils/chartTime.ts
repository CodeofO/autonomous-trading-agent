/*
 * 차트 가로축·십자선 시각을 한국식으로 (2026년 6월 22일 (월) 14:35).
 * 차트 라이브러리 기본값은 'dd MMM 'yy' 라 "22 6월 '26" 처럼 미국식으로 나온다.
 * 차트 조각(lightweight-charts)을 끌어오지 않도록 이 파일은 그 라이브러리를 import 하지 않는다.
 */

/** 봉 하나가 덮는 기간 — 십자선 글자를 고른다 */
export type ChartPeriod = "min" | "day" | "week" | "month";

const DOW = ["일", "월", "화", "수", "목", "금", "토"];

interface Parts {
  y: number;
  m: number;
  d: number;
  hh: number;
  mm: number;
  ss: number;
  dow: number;
  /** 시각까지 있는 값(분봉·자산 추이) */
  clock: boolean;
}

/**
 * 차트가 넘겨주는 시각 → 연·월·일·시·분.
 * - 숫자: 이미 한국 시각 벽시계로 밀어 둔 초 (UTC 로 읽으면 한국 시각)
 * - "YYYY-MM-DD": 일·주·월 봉의 시장 날짜
 * - { year, month, day }: 라이브러리의 BusinessDay
 */
export function timeParts(t: unknown): Parts | null {
  if (typeof t === "number" && Number.isFinite(t)) {
    const dt = new Date(t * 1000);
    return {
      y: dt.getUTCFullYear(),
      m: dt.getUTCMonth() + 1,
      d: dt.getUTCDate(),
      hh: dt.getUTCHours(),
      mm: dt.getUTCMinutes(),
      ss: dt.getUTCSeconds(),
      dow: dt.getUTCDay(),
      clock: true,
    };
  }
  let ymd: [number, number, number] | null = null;
  if (typeof t === "string") {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(t);
    if (m) ymd = [Number(m[1]), Number(m[2]), Number(m[3])];
  } else if (t && typeof t === "object" && "year" in t && "month" in t && "day" in t) {
    const b = t as { year: number; month: number; day: number };
    ymd = [b.year, b.month, b.day];
  }
  if (!ymd) return null;
  const [y, m, d] = ymd;
  return { y, m, d, hh: 0, mm: 0, ss: 0, dow: new Date(Date.UTC(y, m - 1, d)).getUTCDay(), clock: false };
}

const two = (n: number) => String(n).padStart(2, "0");

/** 십자선(마우스를 올렸을 때) 아래 시각 글자 */
export function koCrosshair(t: unknown, period: ChartPeriod = "day"): string {
  const p = timeParts(t);
  if (!p) return String(t);
  if (period === "month") return `${p.y}년 ${p.m}월`;
  const date = `${p.y}년 ${p.m}월 ${p.d}일`;
  if (period === "week") return `${date} 주`;
  const day = `${date} (${DOW[p.dow]})`;
  return p.clock && period === "min" ? `${day} ${two(p.hh)}:${two(p.mm)}` : day;
}

/** 라이브러리 TickMarkType 과 같은 번호 (Year 0, Month 1, DayOfMonth 2, Time 3, TimeWithSeconds 4) */
export const TICK = { year: 0, month: 1, day: 2, time: 3, seconds: 4 } as const;

/** 가로축 눈금 글자: 2026년 · 6월 · 22일 · 14:35 */
export function koTick(t: unknown, type: number): string | null {
  const p = timeParts(t);
  if (!p) return null;
  switch (type) {
    case TICK.year:
      return `${p.y}년`;
    case TICK.month:
      return `${p.m}월`;
    case TICK.day:
      return `${p.d}일`;
    case TICK.time:
      return `${two(p.hh)}:${two(p.mm)}`;
    case TICK.seconds:
      return `${two(p.hh)}:${two(p.mm)}:${two(p.ss)}`;
    default:
      return null;
  }
}

/** 서버 봉 단위 키 → 기간 (1m~60m 분, 1d 일, 1w 주, 1M 월) */
export function periodOf(interval: string | undefined, intraday = false): ChartPeriod {
  if (interval === "1M") return "month";
  if (interval === "1w") return "week";
  if (interval === "1d") return "day";
  if (interval && /^\d+m$/.test(interval)) return "min";
  return intraday ? "min" : "day";
}
