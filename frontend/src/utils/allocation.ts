import type { MarketCode, Position } from "../api/types";

/*
 * 포트폴리오 비중 시각화(도넛·모자이크)에 쓰는 항목 (D-68).
 * 종목은 평가금액, 현금은 현금 그대로 — 비중은 (종목 평가금액 + 현금) 합계 대비 % 라 보유 종목 표의 비중(평가금액 ÷ 총자산)과 같다.
 */

export interface AllocItem {
  /** 화면 안에서 겹치지 않는 열쇠 (시장:종목, cash:시장, other) */
  key: string;
  kind: "stock" | "cash" | "other";
  /** 종목 코드·티커 (현금·기타는 빈 값) */
  symbol: string;
  name: string;
  market?: MarketCode;
  /** 보여 줄 통화로 바꾼 금액 */
  value: number;
  /** 합계 대비 % */
  weight: number;
  /** 수익률 % (현금은 null, 기타는 금액 가중 평균) */
  pnlPct: number | null;
  /** 원래 보유 정보 (빠른 보기 시트용) */
  position?: Position;
  /** 기타로 묶은 종목 수 */
  count?: number;
  /** 기타로 묶은 종목들 (범례에서 펼쳐 본다) */
  children?: AllocItem[];
}

export interface AllocSource {
  market: MarketCode;
  positions: readonly Position[];
  cash: number | null | undefined;
  /** 보여 줄 통화로 바꾸는 배수 (같은 통화면 1, 미장 → 원화면 원/달러 환율) */
  rate?: number;
}

/** 시장별 보유 종목·현금 → 비중 항목 (종목은 금액 큰 순, 현금은 맨 뒤) */
export function allocationItems(sources: readonly AllocSource[]): AllocItem[] {
  const stocks: AllocItem[] = [];
  const cash: AllocItem[] = [];
  const multi = sources.length > 1;
  for (const src of sources) {
    const rate = src.rate ?? 1;
    for (const p of src.positions) {
      const value = (p.market_value || 0) * rate;
      if (!(value > 0)) continue;
      stocks.push({
        key: `${src.market}:${p.symbol}`,
        kind: "stock",
        symbol: p.symbol,
        name: p.name || p.symbol,
        market: src.market,
        value,
        weight: 0,
        pnlPct: Number.isFinite(p.pnl_pct) ? p.pnl_pct : null,
        position: p,
      });
    }
    const c = (src.cash ?? 0) * rate;
    if (c > 0) {
      cash.push({
        key: `cash:${src.market}`,
        kind: "cash",
        symbol: "",
        name: multi ? `현금 (${src.market === "US" ? "미장" : "국장"})` : "현금",
        market: src.market,
        value: c,
        weight: 0,
        pnlPct: null,
      });
    }
  }
  stocks.sort((a, b) => b.value - a.value || a.key.localeCompare(b.key));
  const items = [...stocks, ...cash];
  const total = items.reduce((s, it) => s + it.value, 0);
  for (const it of items) it.weight = total > 0 ? (it.value / total) * 100 : 0;
  return items;
}

/**
 * 도넛은 색이 8개라(범주형 팔레트 한도) 종목이 그보다 많으면 큰 7개만 따로, 나머지는 '기타 N종목'으로 묶는다.
 * 현금은 그대로 둔다.
 */
export const DONUT_SLOTS = 8;

export function foldForDonut(items: readonly AllocItem[], slots = DONUT_SLOTS): AllocItem[] {
  const stocks = items.filter((x) => x.kind === "stock");
  const rest = items.filter((x) => x.kind !== "stock");
  if (stocks.length <= slots) return [...stocks, ...rest];
  const keep = stocks.slice(0, slots - 1);
  const folded = stocks.slice(slots - 1);
  const value = folded.reduce((s, x) => s + x.value, 0);
  const weight = folded.reduce((s, x) => s + x.weight, 0);
  const pnlBase = folded.filter((x) => x.pnlPct != null);
  const pnlValue = pnlBase.reduce((s, x) => s + x.value, 0);
  const other: AllocItem = {
    key: "other",
    kind: "other",
    symbol: "",
    name: `기타 ${folded.length}종목`,
    value,
    weight,
    pnlPct: pnlValue > 0 ? pnlBase.reduce((s, x) => s + (x.pnlPct ?? 0) * x.value, 0) / pnlValue : null,
    count: folded.length,
    children: folded,
  };
  return [...keep, other, ...rest];
}

/**
 * 모자이크 칸 색 단계 (수익률, 한국 관례: 이익 빨강 · 손실 파랑, 0 근처는 회색).
 * -3 ~ +3: 손실 강·중·약 / 보합 / 이익 약·중·강. 경계 ±0.5% · ±2% · ±5%
 */
export function pnlStep(pnl: number | null | undefined): number {
  if (pnl == null || !Number.isFinite(pnl)) return 0;
  const a = Math.abs(pnl);
  const s = a < 0.5 ? 0 : a < 2 ? 1 : a < 5 ? 2 : 3;
  return pnl < 0 && s > 0 ? -s : s;
}
