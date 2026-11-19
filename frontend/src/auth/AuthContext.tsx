import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { ApiClient } from "../api/client";

const SESSION_KEY = "payflow.apiKey";
const KEY_PATTERN = /^pf_[0-9a-f]{12}_[A-Za-z0-9_-]{20,}$/;

interface AuthState {
  apiKey: string | null;
  client: ApiClient;
  baseClient: ApiClient;
  login: (apiKey: string, remember: boolean) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

function readSession(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function writeSession(apiKey: string | null): void {
  try {
    if (apiKey) window.sessionStorage.setItem(SESSION_KEY, apiKey);
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    return;
  }
}

export function normalizeKey(value: string): string {
  const unquote = (text: string) => text.replace(/^["'`]+|["'`]+$/g, "");
  return unquote(unquote(value.replace(/\s+/g, "")).replace(/^api_key=/i, ""));
}

export function isWellFormedKey(value: string): boolean {
  return KEY_PATTERN.test(normalizeKey(value));
}

export function AuthProvider({ children, baseClient }: { children: ReactNode; baseClient?: ApiClient }) {
  const queryClient = useQueryClient();
  const base = useMemo(() => baseClient ?? new ApiClient(), [baseClient]);
  const [apiKey, setApiKey] = useState<string | null>(() => readSession());

  const logout = useCallback(() => {
    writeSession(null);
    setApiKey(null);
    queryClient.clear();
  }, [queryClient]);

  const login = useCallback(
    async (candidate: string, remember: boolean) => {
      const key = normalizeKey(candidate);
      await base.withApiKey(key).verifyKey();
      writeSession(remember ? key : null);
      queryClient.clear();
      setApiKey(key);
    },
    [base, queryClient],
  );

  const value = useMemo<AuthState>(
    () => ({ apiKey, client: base.withApiKey(apiKey), baseClient: base, login, logout }),
    [apiKey, base, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
