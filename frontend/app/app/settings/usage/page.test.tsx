import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useWorkspace } = vi.hoisted(() => ({
  useWorkspace: vi.fn(),
}));

vi.mock("@/lib/workspace-context", () => ({
  useWorkspace,
}));

const mockUsageApi = vi.hoisted(() => ({
  getWorkspaceUsage: vi.fn(),
}));

vi.mock("@/lib/usage-api", () => mockUsageApi);

import WorkspaceUsagePage from "@/app/app/settings/usage/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceUsagePage />
    </QueryClientProvider>,
  );
}

describe("WorkspaceUsagePage", () => {
  beforeEach(() => {
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
    });

    mockUsageApi.getWorkspaceUsage.mockResolvedValue({
      workspace_id: "ws-1",
      plan_name: "Growth Tier",
      dimensions: {
        sent_messages_today: { used: 450, limit: 1000, unit: "messages" },
        active_mailboxes: { used: 3, limit: 10, unit: "mailboxes" },
        leads: { used: 1200, limit: 5000, unit: "leads" },
        active_campaigns: { used: 2, limit: 10, unit: "campaigns" },
        team_members: { used: 4, limit: 10, unit: "members" },
      },
      as_of: "2026-03-01T12:00:00Z",
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders plan name and dimension metrics with progress bars", async () => {
    renderPage();

    expect(await screen.findByText(/Plan: Growth Tier/i)).toBeInTheDocument();
    expect(screen.getByText("Messages Sent Today")).toBeInTheDocument();
    expect(screen.getByText("Connected Mailboxes")).toBeInTheDocument();
    expect(screen.getByText("Stored Leads")).toBeInTheDocument();
    expect(screen.getByText("Active Campaigns")).toBeInTheDocument();
    expect(screen.getByText("Team Members")).toBeInTheDocument();

    expect(screen.getByText("450 / 1,000 messages")).toBeInTheDocument();
    expect(screen.getByText("3 / 10 mailboxes")).toBeInTheDocument();
    expect(screen.getByText("1,200 / 5,000 leads")).toBeInTheDocument();
  });
});
