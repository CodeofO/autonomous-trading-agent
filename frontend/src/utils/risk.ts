import type { RiskConfig } from "../api/types";

/** 리스크 한도 칸 (pct: 화면은 %, 서버는 비율) */
export const RISK_FIELDS: { key: keyof RiskConfig; label: string; pct?: boolean; hint: string }[] = [
  { key: "max_position_weight", label: "종목당 최대 비중", pct: true, hint: "예: 20" },
  { key: "max_order_amount", label: "1회 주문 최대 금액(원)", hint: "예: 1,000,000" },
  { key: "daily_loss_limit", label: "일일 최대 손실", pct: true, hint: "음수, 예: -3 → 당일 신규 매수 중단" },
  { key: "stop_loss", label: "종목별 손절", pct: true, hint: "음수, 예: -5" },
  { key: "take_profit", label: "종목별 익절", pct: true, hint: "양수, 예: 10" },
  { key: "min_order_interval_minutes", label: "같은 종목 최소 매수 간격(분, 매도 제외)", hint: "예: 60" },
  { key: "max_positions", label: "최대 보유 종목 수", hint: "예: 10" },
  { key: "min_cash_ratio", label: "최소 현금 비중", pct: true, hint: "예: 5" },
  { key: "min_hold_days", label: "매수 후 최소 보유 일수 (손절·익절 예외)", hint: "0 = 제한 없음" },
];

/** 전략 리스크 기본값 한 줄 (전략 실험실·리스크 화면) */
export function riskText(r: Partial<RiskConfig> | undefined): string {
  if (!r) return "";
  const pct = (v: number | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const parts = [
    `손절 ${pct(r.stop_loss)}`,
    `익절 +${pct(r.take_profit)}`,
    r.min_hold_days ? `최소 보유 ${r.min_hold_days}일` : "보유 기간 제한 없음",
    `종목당 ${pct(r.max_position_weight)}`,
    `최대 ${r.max_positions ?? "—"}종목`,
  ];
  return parts.join(" · ");
}

export function validateRisk(v: Record<string, number>): string | null {
  if (!(v.max_position_weight > 0 && v.max_position_weight <= 100))
    return "종목당 최대 비중은 0~100% 사이여야 해요";
  if (!(v.max_order_amount > 0)) return "1회 주문 최대 금액은 0보다 커야 해요";
  if (!(v.daily_loss_limit < 0 && v.daily_loss_limit >= -100)) return "일일 최대 손실은 음수여야 해요";
  if (!(v.stop_loss < 0 && v.stop_loss >= -100)) return "손절은 음수여야 해요";
  if (!(v.take_profit > 0)) return "익절은 양수여야 해요";
  if (!(v.min_order_interval_minutes >= 0)) return "최소 주문 간격은 0 이상이어야 해요";
  if (!(v.max_positions >= 1 && Number.isInteger(v.max_positions)))
    return "최대 보유 종목 수는 1 이상의 정수여야 해요";
  if (!(v.min_cash_ratio >= 0 && v.min_cash_ratio < 100)) return "최소 현금 비중은 0~100% 사이여야 해요";
  if (
    v.min_hold_days != null &&
    !(v.min_hold_days >= 0 && v.min_hold_days <= 730 && Number.isInteger(v.min_hold_days))
  )
    return "최소 보유 일수는 0~730 사이의 정수여야 해요";
  return null;
}

/** 금액 칸의 이름·예시는 시장 통화로 (미장: 달러) */
export function riskField(f: (typeof RISK_FIELDS)[number], usd: boolean) {
  if (!usd || f.key !== "max_order_amount") return f;
  return { ...f, label: "1회 주문 최대 금액(달러)", hint: "예: 1,500" };
}
