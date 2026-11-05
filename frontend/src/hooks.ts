import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get } from "./api/client";
import type { LlmCosts, LlmProfiles, MarketCode, MarketIndicators, Portfolio, SystemInfo } from "./api/types";
import { useLiveInterval } from "./live/hooks";
import { useApi } from "./market";

/**
 * 시장 상태 (기본: 지금 시장). 다른 시장 것을 미리 받아 둘 때는 m·주기를 준다.
 * refetchInterval 은 웹소켓이 끊겼을 때의 주기 — 연결돼 있으면 상태 이벤트가 바로 알려 주므로 30~60초로 늦춘다
 */
export const useSystem = (m?: MarketCode, opts: { enabled?: boolean; refetchInterval?: number } = {}) => {
  const api = useApi(m);
  const every = useLiveInterval(api.market, opts.refetchInterval ?? 5000);
  return useQuery({
    queryKey: api.key("system"),
    queryFn: () => api.get<SystemInfo>("/api/system"),
    refetchInterval: every,
    enabled: opts.enabled ?? true,
  });
};

/** 평가금·지표 갱신 주기 (D-61): 장이 닫혀 있어도 시간외·데이마켓·환율로 값이 움직이므로 웹소켓 연결과
 *  관계없이 늘 이 주기로 다시 받는다 (서버는 현재가를 5초, 주요 지표를 15초 캐시한다) */
export const PORTFOLIO_POLL_MS = 10_000;
export const INDICATORS_POLL_MS = 15_000;

/** 계좌 (실시세 평가금 때문에 주기적으로). 체결·주문 이벤트가 오면 바로 다시 받는다 */
export const usePortfolio = (m?: MarketCode) => {
  const api = useApi(m);
  return useQuery({
    queryKey: api.key("portfolio"),
    queryFn: () => api.get<Portfolio>("/api/portfolio"),
    refetchInterval: PORTFOLIO_POLL_MS,
  });
};

// 주요 지표: 실패해도 직전 값을 그대로 두고 오류는 띄우지 않는다 (재시도는 앱 기본값 1회)
export const useMarketIndicators = () =>
  useQuery({
    queryKey: ["market-indicators"],
    queryFn: () => get<MarketIndicators>("/api/market/indicators"),
    refetchInterval: INDICATORS_POLL_MS,
    staleTime: INDICATORS_POLL_MS - 1000,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: false,
  });

// LLM 비용 예상: 자주 바뀌지 않으므로 주기적으로 다시 받지 않는다 (다시 들어왔을 때 1분이 지났으면 한 번)
export const useLlmCosts = (months = 12) =>
  useQuery({
    queryKey: ["llm-costs", months],
    queryFn: () => get<LlmCosts>(`/api/llm/costs?months=${months}`),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

// LLM 무료/유료 설정: 바꾸면 화면에서 직접 다시 받으므로 주기적으로 받지 않는다
export const useLlmProfile = () =>
  useQuery({ queryKey: ["llm-profile"], queryFn: () => get<LlmProfiles>("/api/llm/profile") });

export const DEFAULT_PERSONA = { name: "알파", emoji: "🦉" };

/** 매니저 이름·이모지 — 내용이 같으면 같은 객체 (메모된 채팅 줄이 상태 폴링 때마다 다시 그려지지 않게) */
export function usePersona(sys: SystemInfo | undefined) {
  const name = sys?.persona.name ?? DEFAULT_PERSONA.name;
  const emoji = sys?.persona.emoji ?? DEFAULT_PERSONA.emoji;
  return useMemo(() => ({ name, emoji }), [name, emoji]);
}

// "리서치 과정 보기" — 트레이딩 룸과 대시보드가 같은 설정을 쓴다 (저장소가 막혀 있으면 기본값)
const SHOW_TOOLS_KEY = "chat.showTools";

export function useShowTools(): [boolean, (v: boolean) => void] {
  const [show, setShow] = useState(() => {
    try {
      return window.localStorage.getItem(SHOW_TOOLS_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const update = (v: boolean) => {
    setShow(v);
    try {
      window.localStorage.setItem(SHOW_TOOLS_KEY, String(v));
    } catch {
      /* 저장소 사용 불가 — 이번 화면에서만 유지 */
    }
  };
  return [show, update];
}
