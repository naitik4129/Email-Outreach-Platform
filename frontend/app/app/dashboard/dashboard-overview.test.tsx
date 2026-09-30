import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { getWorkspaceOverview, getDeliverabilityOverview } = vi.hoisted(() => ({
  getWorkspaceOverview: vi.fn(),
  getDeliverabilityOverview: vi.fn(),
}));

vi.mock("@/lib/analytics-api", () => ({
  getWorkspaceOverview,
  getDeliverabilityOverview,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { DashboardOverview } from "./dashboard-overview";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockWorkspace(role = "MANAGER") {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-123",
    activeWorkspace: { role_code: role },
  });
}

function overviewFixture(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function deliverabilityFixture() {
  return {
    workspace_id: "ws-123",
    overall_health: "NEEDS_ATTENTION",
    total_sent: 500,
    bounce_rate: 2.4,
    complaint_rate: 0.05,
    failure_rate: 1.2,
    active_safety_holds: 1,
    warnings: [
      {
        code: "ELEVATED_BOUNCE_RATE",
        level: "WARNING",
        title: "Elevated Bounce Rate",
        message: "Bounce rate on sales@domain.com is 2.4% (> 2.0% warning threshold).",
        metric_value: 2.4,
        threshold: 2.0,
        mailbox_id: "mb-1",
      },
    ],
    mailboxes: [
      {
        mailbox_id: "mb-1",
        email_address: "sales@domain.com",
        provider: "MICROSOFT",
        connection_state: "CONNECTED",
        health_state: "HEALTHY",
        policy_state: "ENABLED",
        health_status: "WARNING",
        sent_count: 500,
        bounce_count: 12,
        bounce_rate: 2.4,
        complaint_count: 0,
        complaint_rate: 0.0,
        failure_count: 6,
        active_safety_holds_count: 1,
        warnings: [],
      },
    ],
    failure_breakdown: [
      {
        category: "RATE_LIMIT",
        count: 4,
        description: "Provider sending rate limit reached or throttled",
      },
    ],
  };
}

describe("DashboardOverview", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders outreach KPIs, honest open-tracking status and top tables", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(overviewFixture());
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Prospects contacted")).toBeInTheDocument();
    expect(await screen.findByText("150")).toBeInTheDocument();
    expect(screen.getByText("Unsubscribes")).toBeInTheDocument();
    expect(screen.getByText("Complaints")).toBeInTheDocument();
    expect(screen.getByText("Send failures")).toBeInTheDocument();
    expect(screen.getByText("Tracking Not Enabled")).toBeInTheDocument();
    expect(screen.getByText("Series A Founders")).toBeInTheDocument();
    expect(screen.getByText("outreach@domain.com")).toBeInTheDocument();
  });

  it("shows the Opened card instead when open tracking is supported", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(
      overviewFixture({ open_tracking_supported: true, opens: 90, open_rate: 30.0 }),
    );
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Opened")).toBeInTheDocument();
    expect(screen.queryByText("Tracking Not Enabled")).not.toBeInTheDocument();
  });

  it("renders deliverability status, warnings, mailbox health and failure breakdown", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(overviewFixture());
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Workspace Deliverability Status")).toBeInTheDocument();
    expect((await screen.findAllByText("Attention Needed")).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("Elevated Bounce Rate")).toBeInTheDocument();
    expect(await screen.findByText("sales@domain.com")).toBeInTheDocument();
    expect(await screen.findByText("Operational Send Failure Breakdown")).toBeInTheDocument();
    expect(await screen.findByText("RATE_LIMIT")).toBeInTheDocument();
  });

  it("still shows outreach performance when the deliverability request fails", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(overviewFixture());
    getDeliverabilityOverview.mockRejectedValue(new Error("deliverability down"));

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Series A Founders")).toBeInTheDocument();
    expect(await screen.findByText(/deliverability down|couldn't load deliverability/i)).toBeInTheDocument();
    expect(screen.queryByText("Workspace Deliverability Status")).not.toBeInTheDocument();
  });

  it("still shows deliverability when the overview request fails", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockRejectedValue(new Error("overview down"));
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Workspace Deliverability Status")).toBeInTheDocument();
    expect(await screen.findByText(/overview down|couldn't load your workspace overview/i)).toBeInTheDocument();
  });

  it("switching the date preset re-queries both endpoints with the same range", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(overviewFixture());
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);
    await screen.findByText("Series A Founders");

    await userEvent.click(screen.getByRole("button", { name: "7 Days" }));

    const lastOverview = getWorkspaceOverview.mock.calls.at(-1);
    const lastDeliverability = getDeliverabilityOverview.mock.calls.at(-1);
    expect(lastOverview?.[1]).toEqual(lastDeliverability?.[1]);
    expect(lastOverview?.[1].startDate).not.toBe(lastOverview?.[1].endDate);
    expect(screen.getByRole("button", { name: "7 Days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("guides a workspace with no sends toward setup", async () => {
    mockWorkspace();
    getWorkspaceOverview.mockResolvedValue(
      overviewFixture({
        emails_sent: 0,
        prospects_contacted: 0,
        replies: 0,
        bounces: 0,
        trend: [],
        top_campaigns: [],
        top_mailboxes: [],
      }),
    );
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    expect(await screen.findByText("Get set up to send")).toBeInTheDocument();
    const cta = screen.getByRole("link", { name: /create campaign/i });
    expect(cta).toHaveAttribute("href", "/app/campaigns/new");
  });

  it("hides the create-campaign action from roles that cannot draft campaigns", async () => {
    mockWorkspace("VIEWER");
    getWorkspaceOverview.mockResolvedValue(
      overviewFixture({ emails_sent: 0, top_campaigns: [], top_mailboxes: [] }),
    );
    getDeliverabilityOverview.mockResolvedValue(deliverabilityFixture());

    renderWithClient(<DashboardOverview />);

    const performance = (await screen.findByText("Top campaigns")).closest("section");
    expect(performance).not.toBeNull();
    expect(
      within(performance as HTMLElement).queryByRole("link", { name: /create campaign/i }),
    ).not.toBeInTheDocument();
  });
});
