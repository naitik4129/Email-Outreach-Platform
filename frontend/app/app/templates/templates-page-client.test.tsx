import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/templates",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { listTemplates, duplicateTemplate, archiveTemplate } = vi.hoisted(() => ({
  listTemplates: vi.fn(),
  duplicateTemplate: vi.fn(),
  archiveTemplate: vi.fn(),
}));

vi.mock("@/lib/templates-api", () => ({
  listTemplates,
  duplicateTemplate,
  archiveTemplate,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { TemplatesPageClient } from "./templates-page-client";

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

describe("TemplatesPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an empty read-only page for a viewer", async () => {
    mockWorkspace("VIEWER");
    listTemplates.mockResolvedValue({ items: [], next_cursor: null });

    renderWithClient(<TemplatesPageClient />);

    expect(await screen.findByText("No templates yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /create template/i })).not.toBeInTheDocument();
  });

  it("shows template list and create button for a member", async () => {
    mockWorkspace("MEMBER");
    listTemplates.mockResolvedValue({
      items: [
        {
          id: "tmpl-1",
          workspace_id: "ws-1",
          name: "Outreach Template 1",
          current_version_id: "ver-1",
          mode: "STANDARD",
          archived_at: null,
          version: 2,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          current_revision: 1,
          subject: "Hello {{first_name}}",
        },
      ],
      next_cursor: null,
    });

    renderWithClient(<TemplatesPageClient />);

    expect(await screen.findByText("Outreach Template 1")).toBeInTheDocument();
    expect(screen.getByText("Hello {{first_name}}")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /create template/i })).toBeInTheDocument();
  });

  it("triggers duplicate template when duplicate button clicked", async () => {
    mockWorkspace("MEMBER");
    listTemplates.mockResolvedValue({
      items: [
        {
          id: "tmpl-1",
          workspace_id: "ws-1",
          name: "To Duplicate",
          current_version_id: "ver-1",
          mode: "STANDARD",
          archived_at: null,
          version: 2,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          current_revision: 1,
          subject: "Test Subject",
        },
      ],
      next_cursor: null,
    });
    duplicateTemplate.mockResolvedValue({
      id: "tmpl-2",
      name: "To Duplicate (Copy)",
    });

    const user = userEvent.setup();
    renderWithClient(<TemplatesPageClient />);

    const dupBtn = await screen.findByTitle("Duplicate");
    await user.click(dupBtn);

    await waitFor(() => {
      expect(duplicateTemplate).toHaveBeenCalledWith("ws-1", "tmpl-1");
    });
  });
});
