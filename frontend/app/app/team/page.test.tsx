import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useWorkspace } = vi.hoisted(() => ({
  useWorkspace: vi.fn(),
}));

vi.mock("@/lib/workspace-context", () => ({
  useWorkspace,
}));

const mockTeamApi = vi.hoisted(() => ({
  listTeamMembers: vi.fn(),
  listInvitations: vi.fn(),
  createInvitation: vi.fn(),
  updateMemberRole: vi.fn(),
  removeMember: vi.fn(),
  transferOwnership: vi.fn(),
  resendInvitation: vi.fn(),
  revokeInvitation: vi.fn(),
}));

vi.mock("@/lib/team-api", () => mockTeamApi);

import TeamPage from "@/app/app/team/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TeamPage />
    </QueryClientProvider>,
  );
}

describe("TeamPage", () => {
  const user = userEvent.setup();

  beforeEach(() => {
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
      activeWorkspace: {
        workspace_id: "ws-1",
        workspace_name: "Acme Corp",
        role_code: "OWNER",
      },
    });

    mockTeamApi.listTeamMembers.mockResolvedValue([
      {
        membership_id: "m-1",
        user_id: "u-1",
        display_name: "Alice Owner",
        email: "alice@acme.com",
        role_code: "OWNER",
        status: "ACTIVE",
        version: 1,
        joined_at: "2026-01-01T00:00:00Z",
      },
      {
        membership_id: "m-2",
        user_id: "u-2",
        display_name: "Bob Member",
        email: "bob@acme.com",
        role_code: "MEMBER",
        status: "ACTIVE",
        version: 1,
        joined_at: "2026-01-02T00:00:00Z",
      },
    ]);

    mockTeamApi.listInvitations.mockResolvedValue([
      {
        id: "inv-1",
        workspace_id: "ws-1",
        email: "charlie@acme.com",
        role_code: "MEMBER",
        status: "PENDING",
        expires_at: "2026-02-01T00:00:00Z",
        created_at: "2026-01-10T00:00:00Z",
      },
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders active members and pending invitations for an OWNER", async () => {
    renderPage();

    expect(await screen.findByText("Alice Owner")).toBeInTheDocument();
    expect(screen.getByText("Bob Member")).toBeInTheDocument();
    expect(screen.getByText("charlie@acme.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /invite teammate/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /transfer ownership/i })).toBeInTheDocument();
  });

  it("allows inviting a teammate", async () => {
    mockTeamApi.createInvitation.mockResolvedValue({
      invitation: { id: "inv-2" },
      invite_token: "mock-token-123",
    });

    renderPage();

    const inviteBtn = await screen.findByRole("button", { name: /invite teammate/i });
    await user.click(inviteBtn);

    const emailInput = screen.getByLabelText(/teammate email/i);
    await user.type(emailInput, "newuser@acme.com");

    const submitBtn = screen.getByRole("button", { name: /send invitation/i });
    await user.click(submitBtn);

    await waitFor(() => {
      expect(mockTeamApi.createInvitation).toHaveBeenCalledWith("ws-1", {
        email: "newuser@acme.com",
        role_code: "MEMBER",
      });
    });

    expect(await screen.findByText(/invitation link generated/i)).toBeInTheDocument();
  });

  it("allows owner to update a member role", async () => {
    mockTeamApi.updateMemberRole.mockResolvedValue({
      membership_id: "m-2",
      role_code: "ADMIN",
      version: 2,
    });

    renderPage();

    const selects = await screen.findAllByRole("combobox");
    // Bob's role selector
    expect(selects.length).toBeGreaterThan(0);
    await user.selectOptions(selects[0], "ADMIN");

    await waitFor(() => {
      expect(mockTeamApi.updateMemberRole).toHaveBeenCalledWith("ws-1", "m-2", {
        role_code: "ADMIN",
        expected_version: 1,
      });
    });
  });
});
