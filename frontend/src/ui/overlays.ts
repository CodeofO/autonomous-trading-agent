import { useSyncExternalStore } from "react";
import type { MarketCode } from "../api/types";

/*
 * 화면 위에 뜨는 것들(⌘K 빠른 이동 · 종목 빠른 보기 시트)을 어디서든 여는 작은 저장소.
 * 그리는 쪽(Layout 의 CommandPaletteHost·StockSheetHost)은 열릴 때만 그 조각을 받는다.
 */

function store<T>(initial: T) {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set: (v: T) => {
      if (Object.is(v, value)) return;
      value = v;
      listeners.forEach((fn) => fn());
    },
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

// ---------- ⌘K 빠른 이동 ----------
const palette = store(false);
export const openPalette = () => palette.set(true);
export const closePalette = () => palette.set(false);
export const togglePalette = () => palette.set(!palette.get());
export const usePaletteOpen = () => useSyncExternalStore(palette.subscribe, palette.get, palette.get);
/** 빠른 이동 조각을 미리 받는다 (검색 버튼에 손이 닿았을 때) */
export const preloadPalette = () => {
  import("../components/CommandPalette").catch(() => {});
};
/** 아래 시트 조각을 미리 받는다 (휴대폰에서 확인 창을 띄울 버튼에 손가락이 닿았을 때) */
export const preloadSheet = () => {
  import("../components/Sheet").catch(() => {});
};

// ---------- 종목 빠른 보기 ----------
/** 시트에 미리 보여 줄 보유 정보 (보유 종목 표에서 열 때) */
export interface QuickPosition {
  quantity?: number;
  avg_price?: number;
  last_price?: number;
  market_value?: number;
  unrealized_pnl?: number;
  pnl_pct: number;
  weight_pct: number;
}

export interface StockTarget {
  symbol: string;
  name?: string;
  market: MarketCode;
  position?: QuickPosition;
}

const stock = store<StockTarget | null>(null);
export const openStockSheet = (t: StockTarget) => stock.set(t);
export const closeStockSheet = () => stock.set(null);
export const useStockSheet = () => useSyncExternalStore(stock.subscribe, stock.get, stock.get);
