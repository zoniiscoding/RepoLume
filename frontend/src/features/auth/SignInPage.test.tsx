import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SignInPage } from "./SignInPage";
import { renderWithApp, testAuth } from "../../test/render";

describe("SignInPage", () => {
  it("starts GitHub authorization without presenting another credential option", () => {
    const signIn = vi.fn();
    renderWithApp(<SignInPage />, { auth: { ...testAuth, state: "anonymous", signIn } });

    fireEvent.click(screen.getByRole("button", { name: /continue with github/i }));

    expect(signIn).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
  });

  it("explains expired session recovery", () => {
    renderWithApp(<SignInPage />, { auth: { ...testAuth, state: "expired" } });
    expect(screen.getByText(/your session ended/i)).toBeInTheDocument();
  });
});
