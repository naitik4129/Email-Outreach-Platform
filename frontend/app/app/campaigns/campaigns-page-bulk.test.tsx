import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/app/campaigns",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { listCampaigns, bulkArchiveCampaigns } = vi.hoisted(() => ({
  listCampaigns: vi.fn(),
  bulkArchiveCampaigns: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => ({ listCampaigns, bulkArchiveCampaigns }));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

// The card has its own tests; here only its selection contract matters.
vi.mock("@/components/campaigns/campaign-card", () => ({
  CampaignCardSkeleton: () => null,
  CampaignCard: (props: {
    campaign: { id: string; name: string };
    selectable?: boolean;
    selected?: boolean;
    mayExecute?: boolean;
    mayErase?: boolean;
    onToggleSelect?: (id: string) => void;
  }) => (
    <article aria-label={props.campaign.name}>
      {props.campaign.name}
      <span data-testid={`caps-${props.campaign.id}`}>
        {String(props.mayExecute)}/{String(props.mayErase)}
      </span>
      {props.selectable ? (
        <input
          type="checkbox"
          aria-label={`Select ${props.campaign.name}`}
          checked={props.selected}
          onChange={() => props.onToggleSelect?.(props.campaign.id)}
        />
      ) : null}
    </article>
  ),
}));

import { CampaignsPageClient } from "./campaigns-page-client";

function item(id: string, name: string, status = "PAUSED", version = 4) {
  return {
    id,
    workspace_id: "ws-1",
    name,
    description: null,
    status,
    campaign_type: "STANDARD",
    draft_sequence_id: null,
    draft_audience_id: null,
    current_settings_id: null,
    version,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CampaignsPageClient />
    </QueryClientProvider>,
  );
}

function asRole(role: string) {
  useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1", activeWorkspace: { role_code: role } });
}

describe("CampaignsPageClient bulk archive and role capabilities", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists everything that is not archived by default", async () => {
    asRole("MANAGER");
    listCampaigns.mockResolvedValue({ items: [item("c1", "One")], next_cursor: null });
    renderPage();
    await screen.findByText("One");
    expect(listCampaigns).toHaveBeenCalledWith("ws-1", {
      limit: 25,
      cursor: null,
      q: null,
      status: null,
    });
    expect(screen.getByText("Not archived")).toBeInTheDocument();
  });

  it("passes each role's capabilities down to the cards", async () => {
    listCampaigns.mockResolvedValue({ items: [item("c1", "One")], next_cursor: null });

    asRole("MEMBER");
    const member = renderPage();
    expect(await screen.findByTestId("caps-c1")).toHaveTextContent("false/false");
    member.unmount();

    asRole("ADMIN");
    renderPage();
    expect(await screen.findByTestId("caps-c1")).toHaveTextContent("true/true");
  });

  it("archives the selected campaigns after confirmation and reports skipped ones", async () => {
    asRole("MANAGER");
    listCampaigns.mockResolvedValue({
      items: [item("c1", "One", "PAUSED", 4), item("c2", "Two", "COMPLETED", 9)],
      next_cursor: null,
    });
    bulkArchiveCampaigns.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
    const user = userEvent.setup();
    renderPage();

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Select" }));
    await user.click(screen.getByRole("checkbox", { name: "Select One" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Two" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Archive selected" }));
    expect(bulkArchiveCampaigns).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/paused first/i);
    await user.click(within(dialog).getByRole("button", { name: "Archive" }));

    await waitFor(() =>
      expect(bulkArchiveCampaigns).toHaveBeenCalledWith("ws-1", [
        { id: "c1", expected_version: 4 },
        { id: "c2", expected_version: 9 },
      ]),
    );
  });

  it("offers no selection to a role that cannot edit campaigns", async () => {
    asRole("VIEWER");
    listCampaigns.mockResolvedValue({ items: [item("c1", "One")], next_cursor: null });
    renderPage();
    await screen.findByText("One");
    expect(screen.queryByRole("button", { name: "Select" })).not.toBeInTheDocument();
  });
});
