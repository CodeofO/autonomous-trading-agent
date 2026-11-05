import { createContext, useContext } from "react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { ApiError, get, marketApi, type MarketApi } from "./api/client";
import type { Currency, MarketCode, MarketInfo, MarketView } from "./api/types";

/**
 * 시장 전환 (화면 맨 위 '전체 · 국장 · 미장').
 *
 * - chosen: 사용자가 고른 보기. 저장소에 남기고 `?m=kr|us|all` 로도 연다
 * - last:   마지막으로 본 시장. '전체'에서 시장 화면(트레이딩 룸·자산 등)을 열면 이 시장으로 보여 준다
 * - view:   지금 화면이 보여 주는 것. '전체'는 한눈에 보기(/)와 공통 화면(/costs)에서만 유지되고,
 *           다른 화면에서는 last 시장이 된다 — 위 전환 버튼은 늘 지금 보이는 시장을 가리킨다
 * - market: 시장별 API·캐시가 쓰는 시장 (view 가 '전체'면 last)
 */

export const MARKET_CODES: MarketCode[] = ["KR", "US"];

export const MARKET_META: Record<MarketCode, { name: string; flag: string; currency: Currency }> = {
  KR: { name: "국장", flag: "🇰🇷", currency: "KRW" },
  US: { name: "미장", flag: "🇺🇸", currency: "USD" },
};

export const currencyOf = (m: MarketCode): Currency => MARKET_META[m].currency;

export const isMarketCode = (v: unknown): v is MarketCode => v === "KR" || v === "US";

/** '전체' 보기로 볼 수 있는 화면: 한눈에 보기(/), 포트폴리오·주문(두 시장을 한 화면에, D-61),
 *  시장과 관계없는 LLM 비용·뉴스 룸 */
export const isAllRoute = (pathname: string) =>
  pathname === "/" ||
  pathname === "/portfolio" ||
  pathname === "/costs" ||
  pathname.startsWith("/costs/") ||
  pathname === "/news" ||
  pathname.startsWith("/news/");

export const VIEW_KEY = "market.view";
export const LAST_KEY = "market.last";

export function parseView(v: string | null | undefined): MarketView | null {
  const u = v?.trim().toUpperCase();
  return u === "ALL" || u === "KR" || u === "US" ? u : null;
}

export function readStore(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeStore(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 저장소 사용 불가 — 이번 화면에서만 유지 */
  }
}

export const viewFromUrl = (search: string) => parseView(new URLSearchParams(search).get("m"));

export const MARKETS_KEY = ["markets"];
const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;

/** GET /api/markets — 시장 목록·장 상태 (예전 서버는 404 → 국장만) */
export const useMarketsQuery = () =>
  useQuery({
    queryKey: MARKETS_KEY,
    queryFn: () => get<MarketInfo[]>("/api/markets"),
    staleTime: 20_000,
    refetchInterval: 60_000,
    retry: (n, e) => !is404(e) && n < 1,
  });

export interface MarketState {
  /** 지금 보이는 보기 (전환 버튼이 가리키는 것) */
  view: MarketView;
  /** 사용자가 고른 보기 (저장되는 값) */
  chosen: MarketView;
  /** 시장별 API·캐시가 쓰는 시장 */
  market: MarketCode;
  /** 마지막으로 본 시장 */
  last: MarketCode;
  /** 켜져 있는 시장 (국장은 늘 있다) */
  available: MarketCode[];
  /** /api/markets 응답 (받기 전·실패 시 undefined) */
  markets: MarketInfo[] | undefined;
  /** 보기를 고른다 (저장). 시장을 고르면 last 도 그 시장 */
  setView: (v: MarketView) => void;
  /** '전체'를 그대로 둔 채 다음에 열 시장 화면을 정한다 (한눈에 보기의 카드·피드 링크를 누를 때) */
  focusMarket: (m: MarketCode) => void;
}

const DEFAULT: MarketState = {
  view: "KR",
  chosen: "KR",
  market: "KR",
  last: "KR",
  available: ["KR"],
  markets: undefined,
  setView: () => {},
  focusMarket: () => {},
};

/** 시장 상태 — 값은 components/MarketProvider 가 채운다 (없으면 국장만 있는 기본값) */
export const MarketContext = createContext<MarketState>(DEFAULT);

export const useMarket = () => useContext(MarketContext);

/** 지금 시장(또는 지정한 시장)에 묶인 API — get/post/put/del 이 주소를 바꾸고, key() 가 쿼리 키에 시장을 붙인다 */
export function useApi(m?: MarketCode): MarketApi {
  const { market } = useMarket();
  return marketApi(m ?? market);
}

/** 지금 시장의 통화 (국장 KRW · 미장 USD) */
export function useCurrency(m?: MarketCode): Currency {
  const { market } = useMarket();
  return currencyOf(m ?? market);
}

/** 이 시장의 정보 (/api/markets) */
export function useMarketInfo(m: MarketCode): MarketInfo | undefined {
  const { markets } = useMarket();
  return markets?.find((x) => x.code === m);
}

/**
 * 이름으로 캐시를 다시 받게 한다. 시장 키(["KR","system"])와 공통 키(["llm-profile"]) 모두 이름으로 찾는다.
 * market 을 주면 그 시장 것만 (공통 키는 늘 포함)
 */
export function invalidateNames(qc: QueryClient, names: string[], market?: MarketCode) {
  return qc.invalidateQueries({
    predicate: (q) => {
      const [k0, k1] = q.queryKey;
      if (isMarketCode(k0)) return (!market || k0 === market) && names.includes(String(k1));
      return names.includes(String(k0));
    },
  });
}
