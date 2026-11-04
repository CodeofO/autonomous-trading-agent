const won = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });

export const fmtWon = (v: number | null | undefined) => (v == null ? "—" : `${won.format(v)}원`);
export const fmtNum = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits }).format(v);
export const fmtPct = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
export const signClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "up" : "down";

// ---------- 시장 통화 (국장 원 · 미장 달러) ----------
type Cur = "KRW" | "USD";
const usdFixed = (digits: number) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const usdCents = usdFixed(2);
const usdSub = usdFixed(4);

function usdText(v: number, f: Intl.NumberFormat): string {
  const body = f.format(Math.abs(v));
  // 반올림해서 0 이 되면 부호를 붙이지 않는다 (-$0.00 대신 $0.00)
  return `${v < 0 && /[1-9]/.test(body) ? "-" : ""}$${body}`;
}

/**
 * 금액: 국장 1,234,567원 · 미장 $1,234.56 (값이 없으면 —).
 * sign: 손익처럼 + 부호도 붙인다 (+12,300원 · +$12.30)
 */
export function money(
  v: number | null | undefined,
  currency: Cur = "KRW",
  opts: { sign?: boolean } = {},
): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const text = currency === "USD" ? usdText(v, usdCents) : fmtWon(v);
  return opts.sign && v > 0 && /[1-9]/.test(text) ? `+${text}` : text;
}

/** 주가: 국장 154,700 (표 안에서는 '원' 없이, krwDigits 자리까지) · 미장 $337.50 ($1 미만은 $0.1234) */
export function price(v: number | null | undefined, currency: Cur = "KRW", krwDigits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (currency === "KRW") return fmtNum(v, krwDigits);
  const a = Math.abs(v);
  return usdText(v, a > 0 && a < 1 ? usdSub : usdCents);
}

type Fmt = (v: number | null | undefined) => string;
/**
 * 통화별 금액·주가 글자 함수 — 같은 통화면 늘 같은 함수.
 * 살아 있는 숫자(AnimatedNumber)에 넘겨도 부모가 다시 그려질 때 숫자 칸이 다시 그려지지 않는다
 */
export const MONEY_FMT: Record<Cur, Fmt> = { KRW: (v) => money(v, "KRW"), USD: (v) => money(v, "USD") };
export const PRICE_FMT: Record<Cur, Fmt> = { KRW: (v) => price(v, "KRW"), USD: (v) => price(v, "USD") };
/** 등락률 글자 (+1.23%) */
export const pctFmt: Fmt = (v) => fmtPct(v);

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

const hm24 = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Seoul",
});

/** 한국 시각 24시간 "HH:MM" (장 시간표·판단 시각처럼 시각끼리 견줄 때) */
export const fmtHm = (iso: string) => hm24.format(new Date(iso));

const kstDay = (ms: number) => Math.floor((ms + 9 * 3600_000) / 86_400_000);

/** 짧은 한국 시각: 오늘 23:30 · 내일 09:00 · 11/07 09:00 (지난 날짜는 날짜와 함께) */
export function fmtWhen(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const diff = kstDay(t) - kstDay(now);
  if (diff === 0) return `오늘 ${fmtHm(iso)}`;
  if (diff === 1) return `내일 ${fmtHm(iso)}`;
  const d = new Date(t + 9 * 3600_000);
  const md = `${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCDate()).padStart(2, "0")}`;
  return `${md} ${fmtHm(iso)}`;
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

// ---------- LLM 비용 ----------
const usd = (max: number, min = 2) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: min, maximumFractionDigits: max });
const usd2 = usd(2);
const usd3 = usd(3);
const usd4 = usd(4);

/** 달러 금액: $1,234.56 · $0.123 · $0.0042 · 아주 작으면 <$0.0001 */
export function fmtUsd(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a === 0) return "$0";
  if (a < 0.0001) return `<${sign}$0.0001`;
  const f = a >= 1 ? usd2 : a >= 0.01 ? usd3 : usd4;
  return `${sign}$${f.format(a)}`;
}

/** 달러 금액을 원으로 (반올림). 0보다 크지만 1원이 안 되면 '1원 미만' */
export function fmtKrwFromUsd(v: number | null | undefined, rate: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const krw = v * rate;
  if (krw > 0 && krw < 0.5) return "1원 미만";
  return fmtWon(Math.round(krw));
}

/** 100만 토큰당 가격: $4.00 · $0.075 */
export const fmtUsdPrice = (v: number | null | undefined) =>
  v == null || !Number.isFinite(v) ? "—" : `$${usd3.format(v)}`;

const tok1 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });
const tok2 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });

/** 토큰 수: 9,876 · 12.3만 · 3.45억 (정확한 값은 fmtNum) */
export function fmtTokens(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a < 10_000) return fmtNum(v);
  if (a < 100_000_000) return `${tok1.format(v / 10_000)}만`;
  return `${tok2.format(v / 100_000_000)}억`;
}

export const ACTION_KO: Record<string, string> = { BUY: "매수", SELL: "매도", HOLD: "유지" };

/** 예약된 판단 일정 이름 (대시보드·한눈에 보기) */
export const JOB_KO: Record<string, string> = {
  morning: "☀️ 장전 판단",
  hourly: "⏰ 정시 판단",
  report: "📊 마감 리포트",
};
export const BASIS_KO: Record<string, string> = { OPEN: "시가", CLOSE: "종가", NOW: "현재가" };

export const STATUS_KO: Record<string, string> = {
  PENDING: "대기",
  SUBMITTED: "접수",
  PARTIALLY_FILLED: "부분체결",
  FILLED: "체결",
  CANCELED: "취소",
  REJECTED: "거부",
};

// ---------- 상대 시각 ("3분 전") ----------
const fullKst = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "Asia/Seoul",
});

/** 정확한 한국 시각 (상대 시각에 마우스를 올리면 보이는 글자) */
export const fmtFull = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? `${fullKst.format(t)} KST` : "";
};

/** 방금 · 3분 전 · 2시간 전 · 어제 21:30 · 3일 전 · (일주일 넘으면) 11. 02. 09:00 */
export function fmtAgo(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "—";
  const s = Math.max(0, (now - t) / 1000);
  if (s < 45) return "방금";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}분 전`;
  if (s < 86_400 && kstDay(t) === kstDay(now)) return `${Math.floor(s / 3600)}시간 전`;
  const days = kstDay(now) - kstDay(t);
  if (days === 1) return `어제 ${fmtHm(iso)}`;
  if (days < 7) return `${days}일 전`;
  return fmtDateTime(iso);
}
