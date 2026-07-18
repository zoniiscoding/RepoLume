import { render } from "@testing-library/react";
import type { PropsWithChildren, ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";

export const testAuth: AuthContextValue = {
  state: "authenticated",
  user: {
    id: "07bfc8e6-cc6a-42c9-8397-205ca4ca5d3d",
    github_user_id: 7,
    github_login: "octocat",
    display_name: "The Octocat",
    avatar_url: null,
    email: null,
  },
  accessToken: "test-access-token",
  signIn: () => undefined,
  signOut: async () => undefined,
  refreshSession: async () => true,
};

export function renderWithApp(
  ui: ReactElement,
  options?: { route?: string; auth?: Partial<AuthContextValue> },
) {
  const value = { ...testAuth, ...options?.auth };
  function Wrapper({ children }: PropsWithChildren): React.JSX.Element {
    return (
      <MemoryRouter initialEntries={[options?.route ?? "/"]}>
        <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
      </MemoryRouter>
    );
  }
  return render(ui, { wrapper: Wrapper });
}
