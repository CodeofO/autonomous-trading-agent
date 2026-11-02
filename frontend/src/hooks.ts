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
