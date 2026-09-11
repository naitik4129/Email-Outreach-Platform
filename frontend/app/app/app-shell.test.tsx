import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace, refresh } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace, refresh }),
  usePathname: () => "/app/dashboard",
}));

const { clearLocalAuthSession, signOutCurrentBrowser } = vi.hoisted(() => ({
  clearLocalAuthSession: vi.fn(),
  signOutCurrentBrowser: vi.fn(),
}));

vi.mock("@/lib/supabase/client", () => ({
  clearLocalAuthSession,
  signOutCurrentBrowser,
}));

const { useWorkspace } = vi.hoisted(() => ({
  useWorkspace: vi.fn(),
}));

vi.mock("@/lib/workspace-context", () => ({
  useWorkspace,
  WorkspaceProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import { ApiError } from "@/lib/api-client";
import { AppShell } from "@/app/app/app-shell";

function renderShell() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AppShell>content</AppShell>
    </QueryClientProvider>,
  );
}

describe("AppShell", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a session-expired card on a 401 workspace error without redirecting itself", async () => {
    useWorkspace.mockReturnValue({
      workspaces: [],
      activeWorkspaceId: null,
      activeWorkspace: null,
      isLoading: false,
      error: new ApiError("Invalid or expired session", 401, "unauthenticated", null),
      switchWorkspace: vi.fn(),
    });

    renderShell();

    expect(await screen.findByText("Session expired")).toBeInTheDocument();
    // apiRequest (lib/api-client.ts) already owns clearing the session and
    // hard-redirecting on a 401 -- this component must not race that with
    // a second, independent recovery flow.
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(clearLocalAuthSession).not.toHaveBeenCalled();
  });

  it("shows a generic error card on a non-401 workspace error", async () => {
    useWorkspace.mockReturnValue({
      workspaces: [],
      activeWorkspaceId: null,
      activeWorkspace: null,
      isLoading: false,
      error: new ApiError("Server error", 500, "internal", null),
      switchWorkspace: vi.fn(),
    });

    renderShell();

    expect(await screen.findByText("Unable to load workspace")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("redirects to onboarding when there is no error and no workspaces", async () => {
    useWorkspace.mockReturnValue({
      workspaces: [],
      activeWorkspaceId: null,
      activeWorkspace: null,
      isLoading: false,
      error: null,
      switchWorkspace: vi.fn(),
    });

    renderShell();

    await vi.waitFor(() => expect(replace).toHaveBeenCalledWith("/onboarding/workspace"));
  });
});
