import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/campaigns",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { listCampaigns } = vi.hoisted(() => ({ listCampaigns: vi.fn() }));

vi.mock("@/lib/campaigns-api", () => ({ listCampaigns }));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { CampaignsPageClient } from "./campaigns-page-client";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockWorkspace(roleCode: string) {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-1",
    activeWorkspace: { role_code: roleCode },
  });
}

describe("CampaignsPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an empty read-only state for a viewer (no create button)", async () => {
    mockWorkspace("VIEWER");
    listCampaigns.mockResolvedValue({ items: [], next_cursor: null });

    renderWithClient(<CampaignsPageClient />);

    expect(await screen.findByText("No campaigns yet")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /create campaign/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the create button for a member", async () => {
    mockWorkspace("MEMBER");
    listCampaigns.mockResolvedValue({ items: [], next_cursor: null });

    renderWithClient(<CampaignsPageClient />);

    expect(await screen.findByText("No campaigns yet")).toBeInTheDocument();
    expect(
      screen.getAllByRole("link", { name: /create campaign/i }).length,
    ).toBeGreaterThan(0);
  });

  it("lists campaigns with their status", async () => {
    mockWorkspace("MEMBER");
    listCampaigns.mockResolvedValue({
      items: [
        {
          id: "camp-1",
          workspace_id: "ws-1",
          name: "Q1 Outbound",
          description: null,
          status: "DRAFT",
          draft_sequence_id: null,
          draft_audience_id: null,
          current_settings_id: null,
          version: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      next_cursor: null,
    });

    renderWithClient(<CampaignsPageClient />);

    expect(await screen.findByText("Q1 Outbound")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
  });
});
