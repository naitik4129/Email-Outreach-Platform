import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
}));

const { getCampaignAnalytics } = vi.hoisted(() => ({ getCampaignAnalytics: vi.fn() }));
vi.mock("@/lib/analytics-api", () => ({ getCampaignAnalytics }));

const { archiveCampaign, duplicateCampaign } = vi.hoisted(() => ({
  archiveCampaign: vi.fn(),
  duplicateCampaign: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => ({ archiveCampaign, duplicateCampaign }));

import { CampaignCard } from "@/components/campaigns/campaign-card";
import type { CampaignListItem } from "@/types/domain";

function campaign(overrides: Partial<CampaignListItem> = {}): CampaignListItem {
  return {
    id: "camp-1",
    workspace_id: "ws-1",
    name: "Q1 Outbound",
    description: "Cold outreach to founders",
    status: "RUNNING",
    campaign_type: "STANDARD",
    draft_sequence_id: null,
    draft_audience_id: null,
    current_settings_id: null,
    version: 4,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

const analytics = {
  sent: 125,
  opened: 22,
  replied: 3,
  bounced: 4,
  open_rate: 18.4,
  reply_rate: 2.4,
  open_tracking_supported: true,
};

function renderCard(item: CampaignListItem, mayDraft = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CampaignCard campaign={item} workspaceId="ws-1" mayDraft={mayDraft} />
    </QueryClientProvider>,
  );
}

describe("CampaignCard", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not request metrics for a draft that has never sent", () => {
    renderCard(campaign({ status: "DRAFT" }));

    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText(/not launched yet/i)).toBeInTheDocument();
    expect(getCampaignAnalytics).not.toHaveBeenCalled();
  });

  it("shows real performance metrics for a launched campaign", async () => {
    getCampaignAnalytics.mockResolvedValue(analytics);
    renderCard(campaign());

    expect(await screen.findByText("125")).toBeInTheDocument();
    expect(screen.getByText("Sent")).toBeInTheDocument();
    expect(screen.getByText("Opened")).toBeInTheDocument();
    expect(screen.getByText("Replies")).toBeInTheDocument();
    expect(screen.getByText("Bounced")).toBeInTheDocument();
    expect(screen.getByText(/open rate/)).toBeInTheDocument();
    expect(getCampaignAnalytics).toHaveBeenCalledWith("ws-1", "camp-1");
  });

  it("hides Opened and falls back to reply rate when open tracking is unsupported", async () => {
    getCampaignAnalytics.mockResolvedValue({ ...analytics, open_tracking_supported: false });
    renderCard(campaign());

    expect(await screen.findByText("125")).toBeInTheDocument();
    expect(screen.queryByText("Opened")).not.toBeInTheDocument();
    expect(screen.getByText(/reply rate/)).toBeInTheDocument();
  });

  it("degrades quietly when metrics fail to load", async () => {
    getCampaignAnalytics.mockRejectedValue(new Error("boom"));
    renderCard(campaign());

    expect(await screen.findByText(/metrics are unavailable/i)).toBeInTheDocument();
    // The campaign itself is still usable.
    expect(screen.getByRole("link", { name: "Q1 Outbound" })).toHaveAttribute(
      "href",
      "/app/campaigns/camp-1/overview",
    );
  });

  it("offers Configure to editors and View to read-only roles", () => {
    getCampaignAnalytics.mockResolvedValue(analytics);
    const { unmount } = renderCard(campaign(), true);
    expect(screen.getByRole("link", { name: "Configure" })).toBeInTheDocument();
    unmount();

    renderCard(campaign(), false);
    expect(screen.getByRole("link", { name: "View" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Configure" })).not.toBeInTheDocument();
  });

  it("only offers duplicate and archive to editors, and archive only for drafts", async () => {
    const user = userEvent.setup();
    getCampaignAnalytics.mockResolvedValue(analytics);

    // Read-only role: view-only menu.
    const readOnly = renderCard(campaign(), false);
    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    expect(screen.getByRole("menuitem", { name: "Analytics" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Duplicate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Archive" })).not.toBeInTheDocument();
    readOnly.unmount();

    // Editor, running campaign: duplicate but no archive.
    const running = renderCard(campaign(), true);
    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    expect(screen.getByRole("menuitem", { name: "Duplicate" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Archive" })).not.toBeInTheDocument();
    running.unmount();

    // Editor, draft: both.
    renderCard(campaign({ status: "DRAFT" }), true);
    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    expect(screen.getByRole("menuitem", { name: "Duplicate" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Archive" })).toBeInTheDocument();
  });

  it("archives only after confirmation, using the campaign's current version", async () => {
    const user = userEvent.setup();
    archiveCampaign.mockResolvedValue({});
    renderCard(campaign({ status: "DRAFT", version: 7 }));

    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    await user.click(screen.getByRole("menuitem", { name: "Archive" }));

    expect(await screen.findByRole("dialog", { name: /archive "q1 outbound"\?/i })).toBeInTheDocument();
    expect(archiveCampaign).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Archive campaign" }));
    expect(archiveCampaign).toHaveBeenCalledWith("ws-1", "camp-1", { expected_version: 7 });
  });

  it("does not archive when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    renderCard(campaign({ status: "DRAFT" }));

    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    await user.click(screen.getByRole("menuitem", { name: "Archive" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(archiveCampaign).not.toHaveBeenCalled();
  });

  it("duplicates and opens the copy", async () => {
    const user = userEvent.setup();
    getCampaignAnalytics.mockResolvedValue(analytics);
    duplicateCampaign.mockResolvedValue({ id: "camp-2", name: "Q1 Outbound (copy)" });
    renderCard(campaign());

    await user.click(screen.getByRole("button", { name: /actions for q1 outbound/i }));
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));

    await vi.waitFor(() => expect(duplicateCampaign).toHaveBeenCalledWith("ws-1", "camp-1"));
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/app/campaigns/camp-2/overview"));
  });
});
