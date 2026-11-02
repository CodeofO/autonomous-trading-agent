import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "./api/client";
import type { Portfolio, SystemInfo } from "./api/types";

export const useSystem = () =>
  useQuery({ queryKey: ["system"], queryFn: () => get<SystemInfo>("/api/system"), refetchInterval: 5000 });

export const usePortfolio = () =>
  useQuery({
    queryKey: ["portfolio"],
    queryFn: () => get<Portfolio>("/api/portfolio"),
    refetchInterval: 10000,
  });

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
