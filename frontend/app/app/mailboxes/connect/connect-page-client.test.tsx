import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/mailboxes/connect",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { startGmailOAuth, startMicrosoftOAuth } = vi.hoisted(() => ({
  startGmailOAuth: vi.fn(),
  startMicrosoftOAuth: vi.fn(),
}));

vi.mock("@/lib/mailboxes-api", () => ({
  startGmailOAuth,
  startMicrosoftOAuth,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { ConnectPageClient } from "./connect-page-client";

function mockWorkspace() {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-test-1",
    activeWorkspace: { role_code: "MANAGER" },
  });
}

describe("ConnectPageClient", () => {
  const originalLocation = window.location;

  afterEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "location", {
      value: originalLocation,
      writable: true,
    });
  });

  it("redirects to the Google authorization URL when Gmail is chosen", async () => {
    mockWorkspace();
    startGmailOAuth.mockResolvedValue({
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=abc",
      expires_at: "2026-01-01T00:00:00Z",
    });
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
    });

    render(<ConnectPageClient />);
    await userEvent.click(screen.getByRole("button", { name: /connect with google/i }));

    expect(startGmailOAuth).toHaveBeenCalledWith("ws-test-1", "/app/mailboxes");
  });

  it("redirects to the Microsoft authorization URL when Microsoft is chosen", async () => {
    mockWorkspace();
    startMicrosoftOAuth.mockResolvedValue({
      authorization_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=abc",
      expires_at: "2026-01-01T00:00:00Z",
    });
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
    });

    render(<ConnectPageClient />);
    await userEvent.click(screen.getByRole("button", { name: /connect with microsoft/i }));

    expect(startMicrosoftOAuth).toHaveBeenCalledWith("ws-test-1", "/app/mailboxes");
  });

  it("shows a safe error message when Microsoft OAuth initiation fails", async () => {
    mockWorkspace();
    const { ApiError } = await import("@/lib/api-client");
    startMicrosoftOAuth.mockRejectedValue(new ApiError("boom", 500, "provider_error", null));

    render(<ConnectPageClient />);
    await userEvent.click(screen.getByRole("button", { name: /connect with microsoft/i }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("links the Custom SMTP card to the configuration form instead of redirecting instantly", () => {
    mockWorkspace();
    render(<ConnectPageClient />);

    const smtpLink = screen.getByRole("link", { name: /configure smtp/i });
    expect(smtpLink).toHaveAttribute("href", "/app/mailboxes/connect/smtp");
  });
});
