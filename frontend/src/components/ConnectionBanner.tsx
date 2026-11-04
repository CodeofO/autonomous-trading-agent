import { useEffect, useState } from "react";
import type { MarketCode } from "../api/types";
import { useConnected, useEventsStore } from "../live/hooks";
import { OFFLINE_STORE } from "../live/store";
import { MARKET_META, useMarket } from "../market";

/** 잠깐 끊겼다 바로 붙는 경우(첫 연결·네트워크 전환)에는 띠를 보이지 않는다 */
export const BANNER_DELAY_MS = 1500;

/**
 * 실시간 연결이 끊긴 동안 상단 바 아래에 붙는 얇은 띠 — "실시간 연결이 끊겼어요 · 다시 연결 중…".
 * 지금 보는 시장(전체 보기면 켜진 시장 모두)의 웹소켓만 본다. 다시 연결되면 바로 사라진다.
 * 본문을 밀지 않도록 상단 바 아래에 겹쳐 그린다 (styles.css .conn-banner)
 */
export function ConnectionBanner() {
  const store = useEventsStore();
  const { view, market, available } = useMarket();
  const kr = useConnected("KR");
  const us = useConnected("US");
  const watched: MarketCode[] = view === "ALL" ? available : [market];
  const down = watched.filter((m) => !(m === "KR" ? kr : us));
  const anyDown = down.length > 0;
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!anyDown) {
      setVisible(false);
      return;
    }
    const t = window.setTimeout(() => setVisible(true), BANNER_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [anyDown]);

  // 실시간 저장소가 없는 곳(화면 하나만 그리는 테스트 등)에서는 그리지 않는다
  if (store === OFFLINE_STORE || !visible || !anyDown) return null;
  // 시장이 둘 이상 켜져 있고 일부만 끊겼으면 어느 시장인지 앞에 붙인다
  const who =
    available.length > 1 && down.length < available.length
      ? `${down.map((m) => `${MARKET_META[m].flag} ${MARKET_META[m].name}`).join(" · ")} `
      : "";
  return (
    <div className="conn-banner" role="status" data-testid="conn-banner">
      <span className="spinner" aria-hidden />
      {who}실시간 연결이 끊겼어요 · 다시 연결 중…
    </div>
  );
}
