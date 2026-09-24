import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { getDeliverabilityOverview } = vi.hoisted(() => ({
  getDeliverabilityOverview: vi.fn(),
}));

vi.mock("@/lib/analytics-api", () => ({
  getDeliverabilityOverview,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { DeliverabilityPageClient } from "./deliverability-page-client";

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

describe("DeliverabilityPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders deliverability status, health badges, warnings, and failure breakdown", async () => {
    mockWorkspace();
    getDeliverabilityOverview.mockResolvedValue({
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
        {
          category: "TEMPORARY_PROVIDER_ERROR",
          count: 2,
          description: "Transient connection or provider server error",
        },
      ],
    });

    renderWithClient(<DeliverabilityPageClient />);

    expect(await screen.findByText("Deliverability Center")).toBeInTheDocument();
    const attentionBadges = await screen.findAllByText("Attention Needed");
    expect(attentionBadges.length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("Elevated Bounce Rate")).toBeInTheDocument();
    expect(await screen.findByText("sales@domain.com")).toBeInTheDocument();

    expect(
      await screen.findByText("Operational Send Failure Breakdown"),
    ).toBeInTheDocument();
    expect(await screen.findByText("RATE_LIMIT")).toBeInTheDocument();
  });
});
