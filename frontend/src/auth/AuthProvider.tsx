import { useCallback, useEffect, useMemo, useState } from "react";
import type { PropsWithChildren } from "react";
import { ApiError, api } from "../api/client";
import type { User } from "../api/contracts";
import { AuthContext } from "./context";
import type { AuthContextValue, AuthState } from "./context";

export function AuthProvider({ children }: PropsWithChildren): React.JSX.Element {
  const [state, setState] = useState<AuthState>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);

  const clearSession = useCallback((nextState: AuthState): void => {
    setAccessToken(null);
    setUser(null);
    setState(nextState);
  }, []);

  const refreshSession = useCallback(async (): Promise<boolean> => {
    try {
      const refreshed = await api.refresh();
      const currentUser = await api.getCurrentUser(refreshed.access_token);
      setAccessToken(refreshed.access_token);
      setUser(currentUser);
      setState("authenticated");
      return true;
    } catch (error) {
      clearSession(error instanceof ApiError && error.status === 401 ? "expired" : "anonymous");
      return false;
    }
  }, [clearSession]);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  const signIn = useCallback((provider: "google" | "github"): void => {
    if (provider === "google") api.startGoogleAuthorization();
    else api.startGitHubAuthorization();
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    try {
      await api.logout(accessToken);
    } finally {
      clearSession("anonymous");
    }
  }, [accessToken, clearSession]);

  const value = useMemo<AuthContextValue>(
    () => ({ state, user, accessToken, signIn, signOut, refreshSession }),
    [accessToken, refreshSession, signIn, signOut, state, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
