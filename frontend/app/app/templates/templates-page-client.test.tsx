import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/templates",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const {
  listTemplates,
  duplicateTemplate,
  archiveTemplate,
  unarchiveTemplate,
  bulkArchiveTemplates,
  bulkUnarchiveTemplates,
} = vi.hoisted(() => ({
  listTemplates: vi.fn(),
  duplicateTemplate: vi.fn(),
  archiveTemplate: vi.fn(),
  unarchiveTemplate: vi.fn(),
  bulkArchiveTemplates: vi.fn(),
  bulkUnarchiveTemplates: vi.fn(),
}));

vi.mock("@/lib/templates-api", () => ({
  listTemplates,
  duplicateTemplate,
  archiveTemplate,
  unarchiveTemplate,
  bulkArchiveTemplates,
  bulkUnarchiveTemplates,
}));

const { purgeTemplate } = vi.hoisted(() => ({ purgeTemplate: vi.fn() }));
vi.mock("@/lib/erasure-api", () => ({ purgeTemplate }));

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

  describe("bulk actions, restore and permanent delete", () => {
    function template(id: string, name: string, archived = false) {
      return {
        id,
        workspace_id: "ws-1",
        name,
        current_version_id: "ver",
        mode: "STANDARD",
        archived_at: archived ? "2026-02-01T00:00:00Z" : null,
        version: 3,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        current_revision: 1,
        subject: "Hi",
      };
    }

    it("archives the selected templates after confirmation", async () => {
      mockWorkspace("MEMBER");
      listTemplates.mockResolvedValue({
        items: [template("t1", "One"), template("t2", "Two")],
        next_cursor: null,
      });
      bulkArchiveTemplates.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });
      const user = userEvent.setup();
      renderWithClient(<TemplatesPageClient />);

      await user.click(await screen.findByRole("checkbox", { name: "Select One" }));
      await user.click(screen.getByRole("checkbox", { name: "Select Two" }));
      expect(screen.getByText("2 selected")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Archive selected" }));
      expect(bulkArchiveTemplates).not.toHaveBeenCalled();
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Archive" }));

      await waitFor(() =>
        expect(bulkArchiveTemplates).toHaveBeenCalledWith("ws-1", [
          { id: "t1", expected_version: 3 },
          { id: "t2", expected_version: 3 },
        ]),
      );
    });

    it("restores an archived template with its current version", async () => {
      mockWorkspace("MEMBER");
      listTemplates.mockResolvedValue({
        items: [template("t1", "Old", true)],
        next_cursor: null,
      });
      unarchiveTemplate.mockResolvedValue({});
      const user = userEvent.setup();
      renderWithClient(<TemplatesPageClient />);

      await user.click(await screen.findByRole("button", { name: /restore/i }));
      await waitFor(() =>
        expect(unarchiveTemplate).toHaveBeenCalledWith("ws-1", "t1", { expected_version: 3 }),
      );
    });

    it("offers permanent delete only to admins, and only after typing the name", async () => {
      listTemplates.mockResolvedValue({
        items: [template("t1", "Old", true)],
        next_cursor: null,
      });
      purgeTemplate.mockResolvedValue({});

      mockWorkspace("MEMBER");
      const member = renderWithClient(<TemplatesPageClient />);
      await screen.findByText("Old");
      expect(screen.queryByRole("button", { name: /delete permanently/i })).not.toBeInTheDocument();
      member.unmount();

      mockWorkspace("ADMIN");
      const user = userEvent.setup();
      renderWithClient(<TemplatesPageClient />);
      await user.click(await screen.findByRole("button", { name: /delete permanently/i }));
      const confirm = within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete permanently" });
      expect(confirm).toBeDisabled();
      await user.type(screen.getByRole("textbox", { name: /type/i }), "Old");
      await user.click(confirm);
      await waitFor(() => expect(purgeTemplate).toHaveBeenCalledWith("ws-1", "t1", "Old"));
    });
  });
});
