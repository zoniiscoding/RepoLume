import { createContext } from "react";
import type { User } from "../api/contracts";

export type AuthState = "loading" | "authenticated" | "anonymous" | "expired";

export interface AuthContextValue {
  state: AuthState;
  user: User | null;
  accessToken: string | null;
  signIn(provider: "google" | "github"): void;
  signOut(): Promise<void>;
  refreshSession(): Promise<boolean>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
