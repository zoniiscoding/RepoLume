import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithApp } from "../../test/render";
import { OAuthCallbackPage } from "./OAuthCallbackPage";

describe("OAuthCallbackPage", () => {
  it("does not expose OAuth values when browser-session recovery fails", async () => {
    renderWithApp(<OAuthCallbackPage />, { auth: { state: "expired" } });

    expect(await screen.findByText(/could not finish your sign-in session/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /return to sign in/i })).toHaveAttribute(
      "href",
      "/signin",
    );
    expect(document.body).not.toHaveTextContent("access_token");
  });

  it("does not start a second session refresh while the auth provider is recovering it", () => {
    const refreshSession = vi.fn(async () => false);

    renderWithApp(<OAuthCallbackPage />, {
      auth: { state: "loading", refreshSession },
    });

    expect(refreshSession).not.toHaveBeenCalled();
    expect(screen.getByText(/finishing your secure session/i)).toBeInTheDocument();
  });
});
