import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SignInPage } from "./SignInPage";
import { renderWithApp, testAuth } from "../../test/render";

describe("SignInPage", () => {
  it("offers independent Google and GitHub authentication", () => {
    const signIn = vi.fn();
    renderWithApp(<SignInPage />, { auth: { ...testAuth, state: "anonymous", signIn } });

    fireEvent.click(screen.getByRole("button", { name: /continue with github/i }));
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));

    expect(signIn).toHaveBeenNthCalledWith(1, "github");
    expect(signIn).toHaveBeenNthCalledWith(2, "google");
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("explains expired session recovery", () => {
    renderWithApp(<SignInPage />, { auth: { ...testAuth, state: "expired" } });
    expect(screen.getByText(/your session ended/i)).toBeInTheDocument();
  });
});
