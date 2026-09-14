import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace, invalidateQueries } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  invalidateQueries: vi.fn(),
}));

let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/mailboxes/connect/microsoft",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => searchParams,
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQueryClient: () => ({ invalidateQueries }),
  };
});

const { completeMicrosoftOAuth } = vi.hoisted(() => ({
  completeMicrosoftOAuth: vi.fn(),
}));

vi.mock("@/lib/mailboxes-api", () => ({ completeMicrosoftOAuth }));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import MicrosoftCallbackPage from "./page";

describe("MicrosoftCallbackPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
    searchParams = new URLSearchParams();
  });

  it("completes the OAuth exchange and redirects to the mailbox on success", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    searchParams = new URLSearchParams({ code: "auth-code", state: "state-token" });
    completeMicrosoftOAuth.mockResolvedValue({
      mailbox_id: "mb-42",
      provider: "MICROSOFT",
      email_address: "user@contoso.com",
      connection_state: "CONNECTED",
      health_state: "HEALTHY",
    });

    render(<MicrosoftCallbackPage />);

    await waitFor(() =>
      expect(completeMicrosoftOAuth).toHaveBeenCalledWith("ws-1", {
        code: "auth-code",
        state: "state-token",
      }),
    );
    expect(await screen.findByText(/connected successfully/i)).toBeInTheDocument();
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["workspace", "ws-1", "mailboxes"],
    });
  });

  it("shows an error state when Microsoft returns an error param", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    searchParams = new URLSearchParams({ error: "access_denied" });

    render(<MicrosoftCallbackPage />);

    expect(await screen.findByText(/connection failed/i)).toBeInTheDocument();
    expect(screen.getByText(/access_denied/)).toBeInTheDocument();
    expect(completeMicrosoftOAuth).not.toHaveBeenCalled();
  });

  it("shows an error state when code or state is missing", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    searchParams = new URLSearchParams();

    render(<MicrosoftCallbackPage />);

    expect(await screen.findByText(/connection failed/i)).toBeInTheDocument();
    expect(completeMicrosoftOAuth).not.toHaveBeenCalled();
  });

  it("shows a safe error message when the backend rejects the exchange", async () => {
    useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1" });
    searchParams = new URLSearchParams({ code: "auth-code", state: "state-token" });
    const { ApiError } = await import("@/lib/api-client");
    completeMicrosoftOAuth.mockRejectedValue(
      new ApiError("OAuth session is invalid", 400, "invalid_oauth_state", null),
    );

    render(<MicrosoftCallbackPage />);

    expect(await screen.findByText("OAuth session is invalid")).toBeInTheDocument();
  });
});
