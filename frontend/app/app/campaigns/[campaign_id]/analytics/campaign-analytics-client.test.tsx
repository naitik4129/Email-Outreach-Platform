import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ campaign_id: "camp-999" }),
}));

const { getCampaignAnalytics, getCampaignSequenceAnalytics } = vi.hoisted(
  () => ({
    getCampaignAnalytics: vi.fn(),
    getCampaignSequenceAnalytics: vi.fn(),
  }),
);

vi.mock("@/lib/analytics-api", () => ({
  getCampaignAnalytics,
  getCampaignSequenceAnalytics,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { CampaignAnalyticsClient } from "./campaign-analytics-client";

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

describe("CampaignAnalyticsClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders campaign performance metrics and step-level attribution", async () => {
    mockWorkspace();
    getCampaignAnalytics.mockResolvedValue({
      campaign_id: "camp-999",
      campaign_name: "Fintech Outreach",
      campaign_status: "RUNNING",
      total_recipients: 50,
      enrolled: 50,
      scheduled: 10,
      sent: 80,
      failed: 2,
      cancelled: 1,
      skipped: 0,
      remaining: 10,
      bounced: 4,
      hard_bounced: 3,
      soft_bounced: 1,
      delivered_estimated: 76,
      opened: 19,
      total_opens: 55,
      open_rate: 25,
      complained: 0,
      unsubscribed: 1,
      replied: 8,
      unique_recipients_contacted: 48,
      unique_recipients_replied: 8,
      bounce_rate: 5,
      complaint_rate: 0.0,
      unsubscribe_rate: 1.25,
      reply_rate: 10.0,
      failure_rate: 2.44,
      open_tracking_supported: true,
      click_tracking_supported: false,
      delivery_confirmation_supported: false,
    });

    getCampaignSequenceAnalytics.mockResolvedValue({
      campaign_id: "camp-999",
      steps: [
        {
          step_id: "step-1",
          position: 1,
          kind: "EMAIL",
          subject: "Introduction to Antigravity",
          scheduled: 0,
          sent: 50,
          failed: 1,
          cancelled: 0,
          bounced: 2,
          replied: 5,
          unsubscribed: 1,
          reply_rate: 10.0,
        },
        {
          step_id: "step-2",
          position: 2,
          kind: "EMAIL",
          subject: "Quick follow-up",
          scheduled: 10,
          sent: 30,
          failed: 1,
          cancelled: 1,
          bounced: 0,
          replied: 3,
          unsubscribed: 0,
          reply_rate: 10.0,
        },
      ],
    });

    renderWithClient(<CampaignAnalyticsClient />);

    expect(await screen.findByText("Total Recipients")).toBeInTheDocument();
    // The six requested metrics: Sent, Bounced, Bounce Rate, Opened, Open Rate, Replied.
    expect(await screen.findByText("80")).toBeInTheDocument(); // Sent
    expect(await screen.findByText("48 leads contacted")).toBeInTheDocument();
    expect(await screen.findByText("Bounced")).toBeInTheDocument();
    expect(await screen.findByText("3 hard · 1 soft")).toBeInTheDocument();
    expect(await screen.findByText("bounce rate")).toBeInTheDocument();
    expect(await screen.findByText("Opened")).toBeInTheDocument();
    expect(await screen.findByText("19")).toBeInTheDocument();
    expect(await screen.findByText("55 total opens")).toBeInTheDocument();
    expect(await screen.findByText("open rate")).toBeInTheDocument();
    expect(await screen.findByText("Replied")).toBeInTheDocument();
    expect(await screen.findByText("Sequence Step Performance")).toBeInTheDocument();
    expect(
      await screen.findByText("Introduction to Antigravity"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Quick follow-up")).toBeInTheDocument();
  });
});


describe("CampaignAnalyticsClient open tracking", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("says open tracking is off instead of showing a misleading 0% open rate", async () => {
    mockWorkspace();
    getCampaignAnalytics.mockResolvedValue({
      campaign_id: "camp-999",
      campaign_name: "No pixel",
      campaign_status: "RUNNING",
      total_recipients: 10,
      enrolled: 10,
      scheduled: 0,
      sent: 10,
      failed: 0,
      cancelled: 0,
      skipped: 0,
      remaining: 0,
      bounced: 0,
      complained: 0,
      unsubscribed: 0,
      replied: 1,
      unique_recipients_contacted: 10,
      unique_recipients_replied: 1,
      bounce_rate: 0,
      complaint_rate: 0,
      unsubscribe_rate: 0,
      reply_rate: 10,
      failure_rate: 0,
      open_tracking_supported: false,
      click_tracking_supported: false,
      delivery_confirmation_supported: false,
    });
    getCampaignSequenceAnalytics.mockResolvedValue({ campaign_id: "camp-999", steps: [] });

    renderWithClient(<CampaignAnalyticsClient />);

    expect(await screen.findByText("Open tracking is off")).toBeInTheDocument();
    expect(screen.queryByText("open rate")).not.toBeInTheDocument();
    expect(await screen.findByText("Replied")).toBeInTheDocument();
  });
});
