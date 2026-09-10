import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { getWorkspace, updateWorkspace } = vi.hoisted(() => ({
  getWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
}));
vi.mock("@/lib/workspaces-api", () => ({ getWorkspace, updateWorkspace }));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { WorkspaceSettings } from "@/app/app/dashboard/workspace-settings";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("WorkspaceSettings permission-sensitive UI", () => {
  afterEach(() => vi.clearAllMocks());

  it("shows read-only messaging and no edit control for a VIEWER", async () => {
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
      activeWorkspace: { role_code: "VIEWER" },
    });
    getWorkspace.mockResolvedValue({
      id: "ws-1",
      name: "Acme",
      status: "ACTIVE",
      defaults: {},
      version: 1,
      role_code: "VIEWER",
    });

    renderWithClient(<WorkspaceSettings />);

    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit workspace name/i })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("lets an OWNER edit and save a new workspace name", async () => {
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
      activeWorkspace: { role_code: "OWNER" },
    });
    getWorkspace.mockResolvedValue({
      id: "ws-1",
      name: "Acme",
      status: "ACTIVE",
      defaults: {},
      version: 1,
      role_code: "OWNER",
    });
    updateWorkspace.mockResolvedValue({
      id: "ws-1",
      name: "Acme Renamed",
      status: "ACTIVE",
      defaults: {},
      version: 2,
      role_code: "OWNER",
    });

    const user = userEvent.setup();
    renderWithClient(<WorkspaceSettings />);

    await screen.findByText("Acme");
    await user.click(screen.getByRole("button", { name: /edit workspace name/i }));
    const input = screen.getByLabelText(/workspace name/i);
    await user.clear(input);
    await user.type(input, "Acme Renamed");
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(updateWorkspace).toHaveBeenCalledWith("ws-1", {
        name: "Acme Renamed",
        expected_version: 1,
      }),
    );
    expect(await screen.findByText("Acme Renamed")).toBeInTheDocument();
  });
});
