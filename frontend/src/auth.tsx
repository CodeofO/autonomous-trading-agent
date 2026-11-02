import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { getToken, setToken, setUnauthorizedHandler } from "./api/client";

interface AuthState {
  token: string | null;
  login: (token: string) => void;
  logout: () => void;
}

const AuthCtx = createContext<AuthState>({ token: null, login: () => {}, logout: () => {} });

export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTok] = useState<string | null>(() => getToken());
  const login = useCallback((t: string) => {
    setToken(t);
    setTok(t);
  }, []);
  const logout = useCallback(() => {
    setToken(null);
    setTok(null);
  }, []);
  useEffect(() => setUnauthorizedHandler(logout), [logout]);
  return <AuthCtx.Provider value={{ token, login, logout }}>{children}</AuthCtx.Provider>;
}
