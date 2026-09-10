import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const signUp = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { signUp } }),
}));

import SignupPage from "@/app/auth/signup/page";

describe("SignupPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("rejects a short password before calling Supabase", async () => {
    const user = userEvent.setup();
    render(<SignupPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "short");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(
      await screen.findByText("Password must be at least 8 characters"),
    ).toBeInTheDocument();
    expect(signUp).not.toHaveBeenCalled();
  });

  it("shows the same generic success message whether or not the account already exists", async () => {
    signUp.mockResolvedValue({ error: null });
    const user = userEvent.setup();
    render(<SignupPage />);

    await user.type(screen.getByLabelText(/email/i), "existing@example.com");
    await user.type(screen.getByLabelText(/password/i), "longenoughpassword");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(await screen.findByText(/check your email/i)).toBeInTheDocument();
  });

  it("shows a generic error banner on provider failure without leaking details", async () => {
    signUp.mockResolvedValue({ error: { message: "duplicate key value violates ..." } });
    const user = userEvent.setup();
    render(<SignupPage />);

    await user.type(screen.getByLabelText(/email/i), "user@example.com");
    await user.type(screen.getByLabelText(/password/i), "longenoughpassword");
    await user.click(screen.getByRole("button", { name: /sign up/i }));

    expect(
      await screen.findByText("We couldn't create your account. Please try again."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/duplicate key/i)).not.toBeInTheDocument();
  });
});
