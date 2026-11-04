import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get } from "./api/client";
import type { LlmCosts, LlmProfiles, MarketIndicators, Portfolio, SystemInfo } from "./api/types";

export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: () => get<SystemInfo>("/api/system"), refetchInterval: 5000 });

export const usePortfolio = () =>
  useQuery({
    queryKey: ["portfolio"],
    queryFn: () => get<Portfolio>("/api/portfolio"),
    refetchInterval: 10000,
  });

// 주요 지표: 서버가 60초 캐시하므로 1분마다. 실패해도 직전 값을 그대로 두고 오류는 띄우지 않는다 (재시도는 앱 기본값 1회)
export const useMarketIndicators = () =>
  useQuery({
    queryKey: ["market-indicators"],
    queryFn: () => get<MarketIndicators>("/api/market/indicators"),
    refetchInterval: 60_000,
    staleTime: 55_000,
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
