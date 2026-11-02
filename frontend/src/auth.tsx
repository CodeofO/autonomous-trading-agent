import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, getToken, setToken, setUnauthorizedHandler } from "./api/client";

interface AuthState {
  token: string | null;
  /** 서버가 로그인을 요구하는지 (기본 false — 로컬 전용) */
  authEnabled: boolean;
  ready: boolean;
  login: (token: string) => void;
  logout: () => void;
}

const AuthCtx = createContext<AuthState>({
  token: null,
  authEnabled: false,
  ready: true,
  login: () => {},
  logout: () => {},
});

export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState<string | null>(() => getToken());
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => get<{ auth_enabled: boolean; setup_required: boolean }>("/api/auth/status"),
    staleTime: Infinity,
  });
  const login = useCallback((t: string) => {
    setToken(t);
    setTok(t);
  }, []);
  const logout = useCallback(() => {
    setToken(null);
    setTok(null);
  }, []);
  useEffect(() => setUnauthorizedHandler(logout), [logout]);
  const value: AuthState = {
    token,
    authEnabled: status.data?.auth_enabled ?? false,
    ready: !status.isLoading,
    login,
    logout,
  };
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
