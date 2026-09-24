import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { getWorkspaceOverview } = vi.hoisted(() => ({
  getWorkspaceOverview: vi.fn(),
}));

vi.mock("@/lib/analytics-api", () => ({
  getWorkspaceOverview,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { AnalyticsPageClient } from "./analytics-page-client";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockWorkspace() {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-123",
    activeWorkspace: { role_code: "MANAGER" },
  });
}

describe("AnalyticsPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders workspace outreach analytics with KPI cards and honest open tracking status", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue({
      workspace_id: "ws-123",
      start_date: "2026-05-01",
      end_date: "2026-05-30",
      timezone: "UTC",
      prospects_contacted: 150,
      emails_sent: 300,
      replies: 24,
      bounces: 6,
      unsubscribes: 2,
      complaints: 0,
      failed_sends: 3,
      reply_rate: 8.0,
      bounce_rate: 2.0,
      complaint_rate: 0.0,
      unsubscribe_rate: 0.67,
      open_tracking_supported: false,
      click_tracking_supported: false,
      delivery_confirmation_supported: false,
      trend: [
        { date: "2026-05-28", sent: 100, replies: 8, bounces: 2 },
        { date: "2026-05-29", sent: 100, replies: 10, bounces: 2 },
        { date: "2026-05-30", sent: 100, replies: 6, bounces: 2 },
      ],
      top_campaigns: [
        {
          campaign_id: "camp-1",
          name: "Series A Founders",
          status: "RUNNING",
          sent: 200,
          replies: 20,
          reply_rate: 10.0,
          bounces: 4,
          bounce_rate: 2.0,
        },
      ],
      top_mailboxes: [
        {
          mailbox_id: "mb-1",
          email_address: "outreach@domain.com",
          provider: "GMAIL",
          sent: 300,
          replies: 24,
          reply_rate: 8.0,
          bounces: 6,
          bounce_rate: 2.0,
        },
      ],
    });

    renderWithClient(<AnalyticsPageClient />);

    expect(await screen.findByText("Outreach Analytics")).toBeInTheDocument();
    expect(await screen.findByText("150")).toBeInTheDocument(); // Prospects Contacted
    const sentElements = await screen.findAllByText("300"); // Emails Sent card & mailbox table
    expect(sentElements.length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("Tracking Not Enabled")).toBeInTheDocument(); // Honest open/click badge
    expect(await screen.findByText("Series A Founders")).toBeInTheDocument();

    expect(await screen.findByText("outreach@domain.com")).toBeInTheDocument();
  });

  it("handles date preset switching", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue({
      workspace_id: "ws-123",
      start_date: "2026-05-24",
      end_date: "2026-05-30",
      timezone: "UTC",
      prospects_contacted: 50,
      emails_sent: 100,
      replies: 5,
      bounces: 1,
      unsubscribes: 0,
      complaints: 0,
      failed_sends: 0,
      reply_rate: 5.0,
      bounce_rate: 1.0,
      complaint_rate: 0.0,
      unsubscribe_rate: 0.0,
      open_tracking_supported: false,
      click_tracking_supported: false,
      delivery_confirmation_supported: false,
      trend: [],
      top_campaigns: [],
      top_mailboxes: [],
    });

    renderWithClient(<AnalyticsPageClient />);

    expect(await screen.findByText("Outreach Analytics")).toBeInTheDocument();

    const sevenDaysBtn = screen.getByRole("button", { name: "7 Days" });
    await userEvent.click(sevenDaysBtn);

    expect(getWorkspaceOverview).toHaveBeenCalled();
  });
});
