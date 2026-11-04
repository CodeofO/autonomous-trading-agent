import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { MarketCode, MarketView } from "../api/types";
import {
  isAllRoute,
  isMarketCode,
  LAST_KEY,
  MARKET_CODES,
  MarketContext,
  parseView,
  readStore,
  useMarketsQuery,
  VIEW_KEY,
  viewFromUrl,
  writeStore,
  type MarketState,
} from "../market";

/** 시장 보기 상태를 채운다 (규칙은 src/market.ts 맨 위 설명). 라우터·쿼리 클라이언트 안에 둔다 */
export function MarketProvider({ children, initial }: { children: ReactNode; initial?: MarketView }) {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const q = useMarketsQuery();
  const [chosen, setChosen] = useState<MarketView>(
    () => initial ?? viewFromUrl(search) ?? parseView(readStore(VIEW_KEY)) ?? "ALL",
  );
  const [last, setLast] = useState<MarketCode>(() => {
    if (isMarketCode(chosen)) return chosen;
    const stored = parseView(readStore(LAST_KEY));
    return isMarketCode(stored) ? stored : "KR";
  });

  const pick = useCallback((v: MarketView) => {
    setChosen(v);
    writeStore(VIEW_KEY, v);
    if (isMarketCode(v)) {
      setLast(v);
      writeStore(LAST_KEY, v);
    }
  }, []);

  // 딥 링크 ?m=kr|us|all — 적용하고 주소에서는 지운다 (새로고침해도 저장된 값으로 같은 화면)
  useEffect(() => {
    const v = viewFromUrl(search);
    if (!v) return;
    pick(v);
    const params = new URLSearchParams(search);
    params.delete("m");
    const rest = params.toString();
    navigate({ pathname, search: rest ? `?${rest}` : "" }, { replace: true });
  }, [search, pathname, navigate, pick]);

  const markets = q.data;
  const available = useMemo<MarketCode[]>(() => {
    // 받기 전에는 두 시장 다 있다고 보고 그린다 (대부분 그렇다 — 버튼이 늦게 생기며 화면이 튀지 않게).
    // 못 받으면(예전 서버 404 등) 국장만
    if (!markets) return q.isError ? ["KR"] : MARKET_CODES;
    return MARKET_CODES.filter((c) => c === "KR" || markets.some((m) => m.code === c && m.enabled));
  }, [markets, q.isError]);

  const lastOk: MarketCode = available.includes(last) ? last : "KR";
  let view: MarketView = available.length < 2 ? "KR" : chosen;
  if (isMarketCode(view) && !available.includes(view)) view = "KR";
  if (view === "ALL" && !isAllRoute(pathname)) view = lastOk;
  const market: MarketCode = view === "ALL" ? lastOk : view;

  const focusMarket = useCallback((m: MarketCode) => {
    setLast(m);
    writeStore(LAST_KEY, m);
  }, []);

  const value = useMemo<MarketState>(
    () => ({ view, chosen, market, last: lastOk, available, markets, setView: pick, focusMarket }),
    [view, chosen, market, lastOk, available, markets, pick, focusMarket],
  );
  return <MarketContext.Provider value={value}>{children}</MarketContext.Provider>;
}
