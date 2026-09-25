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

// Every profile field is present on the wire, null when not provided.
const emptyProfile = {
  phone: null,
  department: null,
  experience_years: null,
  linkedin_url: null,
  website: null,
  city: null,
  state: null,
  country: null,
  company_website: null,
  company_industry: null,
  company_founded_year: null,
  company_linkedin_url: null,
};

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
      ...emptyProfile,
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
        ...emptyProfile,
        custom_fields: {},
        list_id: "list-1",
      }),
    );
    expect(listLeads).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({ limit: 25 }),
    );
  });

  it("sends profile fields as trimmed strings, numbers, or null", async () => {
    mockWorkspace("MEMBER");
    listLeads.mockResolvedValue({ items: [], next_cursor: null });
    listLeadLists.mockResolvedValue({ items: [], next_cursor: null });
    createLead.mockResolvedValue({});

    const user = userEvent.setup();
    renderWithClient(<LeadsPageClient />);

    await user.click(await screen.findByRole("button", { name: /add lead/i }));
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Job title"), "Founder");
    await user.type(screen.getByLabelText("Phone"), "  +1 555 123 4567 ");
    await user.type(screen.getByLabelText("Experience (years)"), "7");
    await user.type(screen.getByLabelText("City"), "Berlin");
    await user.type(screen.getByLabelText("Company founded year"), "1999");
    await user.type(screen.getByLabelText("Company website"), "acme.example.com");
    await user.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() =>
      expect(createLead).toHaveBeenCalledWith("ws-1", {
        email: "ada@example.com",
        first_name: null,
        last_name: null,
        company: null,
        title: "Founder",
        ...emptyProfile,
        phone: "+1 555 123 4567",
        experience_years: 7,
        city: "Berlin",
        company_founded_year: 1999,
        company_website: "acme.example.com",
        custom_fields: {},
        list_id: null,
      }),
    );
  });

  it("shows job title, location and company columns for existing leads", async () => {
    mockWorkspace("MEMBER");
    listLeads.mockResolvedValue({
      items: [
        {
          id: "lead-1",
          email: "ada@example.com",
          first_name: "Ada",
          last_name: "Lovelace",
          company: "Analytical Engines",
          title: "Founder",
          ...emptyProfile,
          city: "London",
          country: "United Kingdom",
          list_count: 0,
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: "lead-2",
          email: "grace@example.com",
          first_name: "Grace",
          last_name: "Hopper",
          company: null,
          title: null,
          ...emptyProfile,
          list_count: 0,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      next_cursor: null,
    });
    listLeadLists.mockResolvedValue({ items: [], next_cursor: null });

    renderWithClient(<LeadsPageClient />);

    expect(await screen.findByText("London, United Kingdom")).toBeInTheDocument();
    expect(screen.getByText("Founder")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Job title" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Location" })).toBeInTheDocument();
  });
});
