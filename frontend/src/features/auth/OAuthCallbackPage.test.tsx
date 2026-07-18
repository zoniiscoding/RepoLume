import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithApp } from "../../test/render";
import { OAuthCallbackPage } from "./OAuthCallbackPage";

describe("OAuthCallbackPage", () => {
  it("does not expose OAuth values when browser-session recovery fails", async () => {
    renderWithApp(<OAuthCallbackPage />, { auth: { refreshSession: async () => false } });

    expect(await screen.findByText(/could not finish your github session/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /return to sign in/i })).toHaveAttribute(
      "href",
      "/signin",
    );
    expect(document.body).not.toHaveTextContent("access_token");
  });
});
