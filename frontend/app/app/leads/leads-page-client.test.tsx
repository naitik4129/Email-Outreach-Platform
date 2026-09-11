import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/leads",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { createLead, listLeadLists, listLeads } = vi.hoisted(() => ({
  createLead: vi.fn(),
  listLeadLists: vi.fn(),
  listLeads: vi.fn(),
}));

vi.mock("@/lib/leads-api", () => ({
  createLead,
  listLeadLists,
  listLeads,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { LeadsPageClient } from "./leads-page-client";

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

describe("LeadsPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an empty read-only page for a viewer", async () => {
    mockWorkspace("VIEWER");
    listLeads.mockResolvedValue({ items: [], next_cursor: null });
    listLeadLists.mockResolvedValue({ items: [], next_cursor: null });

    renderWithClient(<LeadsPageClient />);

    expect(await screen.findByText("No leads yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add lead/i })).not.toBeInTheDocument();
  });

  it("lets a member create a lead without fetching all records", async () => {
    mockWorkspace("MEMBER");
    listLeads.mockResolvedValue({ items: [], next_cursor: null });
    listLeadLists.mockResolvedValue({
      items: [
        {
          id: "list-1",
          workspace_id: "ws-1",
          name: "Founders",
          archived_at: null,
          membership_revision: 1,
          member_count: 0,
          version: 1,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ],
      next_cursor: null,
    });
    createLead.mockResolvedValue({
      id: "lead-1",
      workspace_id: "ws-1",
      email: "ada@example.com",
      canonical_address: "ada@example.com",
      normalization_version: 1,
      first_name: "Ada",
      last_name: "Lovelace",
      company: null,
      title: null,
      custom_fields: {},
      status: "ACTIVE",
      validation_status: "UNKNOWN",
      validated_at: null,
      contact_revision: 1,
      archived_at: null,
      version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    const user = userEvent.setup();
    renderWithClient(<LeadsPageClient />);

    await user.click(await screen.findByRole("button", { name: /add lead/i }));
    await user.type(screen.getByLabelText(/email/i), "ada@example.com");
    await user.type(screen.getByLabelText(/first name/i), "Ada");
    await user.selectOptions(screen.getByLabelText("List"), "list-1");
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(createLead).toHaveBeenCalledWith("ws-1", {
        email: "ada@example.com",
        first_name: "Ada",
        last_name: null,
        company: null,
        title: null,
        custom_fields: {},
        list_id: "list-1",
      }),
    );
    expect(listLeads).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({ limit: 25 }),
    );
  });
});
