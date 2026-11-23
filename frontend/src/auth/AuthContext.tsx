import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { api, configureAuth } from "../api/client";

// Token held in memory and mirrored to sessionStorage, so a reload keeps the session and closing the tab ends it.
const KEY = "pocketwatch.token";

function readStored(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function store(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: the session just won't survive a reload
  }
}

interface AuthValue {
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(readStored);

  const logout = useCallback(() => {
    store(null);
    setToken(null);
    configureAuth({ token: null, onUnauthorized: () => {} });
    navigate("/login", { replace: true });
  }, [navigate]);

  // Configure the client during render so the very first request already carries the token.
  configureAuth({ token, onUnauthorized: logout });

  const login = useCallback(async (email: string, password: string) => {
    const res = await api<{ access_token: string }>("/auth/login", { method: "POST", form: { username: email, password } });
    store(res.access_token);
    configureAuth({ token: res.access_token, onUnauthorized: logout });
    setToken(res.access_token);
  }, [logout]);

  const register = useCallback(async (email: string, password: string) => {
    await api("/auth/register", { method: "POST", json: { email, password } });
  }, []);

  const value = useMemo(() => ({ token, login, register, logout }), [token, login, register, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
