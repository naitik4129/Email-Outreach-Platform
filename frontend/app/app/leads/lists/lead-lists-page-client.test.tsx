import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace, search } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  search: { value: "" },
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/leads/lists",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(search.value),
}));

const {
  listLeadLists,
  listLeads,
  createLeadList,
  addLeadListMember,
  archiveLeadList,
  unarchiveLeadList,
  bulkArchiveLeadLists,
  bulkUnarchiveLeadLists,
} = vi.hoisted(() => ({
  listLeadLists: vi.fn(),
  listLeads: vi.fn(),
  createLeadList: vi.fn(),
  addLeadListMember: vi.fn(),
  archiveLeadList: vi.fn(),
  unarchiveLeadList: vi.fn(),
  bulkArchiveLeadLists: vi.fn(),
  bulkUnarchiveLeadLists: vi.fn(),
}));

vi.mock("@/lib/leads-api", () => ({
  listLeadLists,
  listLeads,
  createLeadList,
  addLeadListMember,
  archiveLeadList,
  unarchiveLeadList,
  bulkArchiveLeadLists,
  bulkUnarchiveLeadLists,
}));

const { purgeLeadList, bulkPurgeLeadLists } = vi.hoisted(() => ({
  purgeLeadList: vi.fn(),
  bulkPurgeLeadLists: vi.fn(),
}));
vi.mock("@/lib/erasure-api", () => ({
  purgeLeadList,
  bulkPurgeLeadLists,
  BULK_DELETE_PHRASE: "DELETE",
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { LeadListsPageClient } from "./lead-lists-page-client";

function list(id: string, name: string, archived = false) {
  return {
    id,
    workspace_id: "ws-1",
    name,
    archived_at: archived ? "2026-02-01T00:00:00Z" : null,
    membership_revision: 1,
    member_count: 4,
    version: 6,
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
      <LeadListsPageClient />
    </QueryClientProvider>,
  );
}

function asRole(role: string) {
  useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1", activeWorkspace: { role_code: role } });
}

describe("LeadListsPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
    search.value = "";
  });

  it("asks for active lists by default and switches to the archived filter", async () => {
    asRole("MEMBER");
    listLeadLists.mockResolvedValue({ items: [list("l1", "Founders")], next_cursor: null });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Founders");
    expect(listLeadLists).toHaveBeenCalledWith("ws-1", {
      limit: 25,
      cursor: null,
      status: "ACTIVE",
    });

    await user.selectOptions(screen.getByLabelText("List status"), "ARCHIVED");
    expect(push).toHaveBeenCalledWith("/app/leads/lists?status=ARCHIVED");
  });

  it("archives the selected lists after confirmation, with their versions", async () => {
    asRole("MEMBER");
    listLeadLists.mockResolvedValue({
      items: [list("l1", "Founders"), list("l2", "Agencies")],
      next_cursor: null,
    });
    bulkArchiveLeadLists.mockResolvedValue({ results: [], succeeded: 1, failed: 0 });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("checkbox", { name: "Select Agencies" }));
    await user.click(screen.getByRole("button", { name: "Archive selected" }));
    expect(bulkArchiveLeadLists).not.toHaveBeenCalled();
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Archive" }));

    await waitFor(() =>
      expect(bulkArchiveLeadLists).toHaveBeenCalledWith("ws-1", [
        { id: "l2", expected_version: 6 },
      ]),
    );
  });

  it("restores an archived list", async () => {
    search.value = "status=ARCHIVED";
    asRole("MEMBER");
    listLeadLists.mockResolvedValue({ items: [list("l1", "Old", true)], next_cursor: null });
    unarchiveLeadList.mockResolvedValue({});
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /restore/i }));
    await waitFor(() => expect(unarchiveLeadList).toHaveBeenCalledWith("ws-1", "l1", 6));
  });

  it("lets only admins delete an archived list, after typing its name", async () => {
    search.value = "status=ARCHIVED";
    listLeadLists.mockResolvedValue({ items: [list("l1", "Old", true)], next_cursor: null });
    purgeLeadList.mockResolvedValue({});

    asRole("MANAGER");
    const manager = renderPage();
    await screen.findByText("Old");
    expect(screen.queryByRole("button", { name: /delete old permanently/i })).not.toBeInTheDocument();
    manager.unmount();

    asRole("ADMIN");
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /delete old permanently/i }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Delete permanently" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "Old");
    await user.click(confirm);
    await waitFor(() => expect(purgeLeadList).toHaveBeenCalledWith("ws-1", "l1", "Old"));
  });

  it("deletes an active list by archiving it first, then purging it", async () => {
    asRole("ADMIN");
    listLeadLists.mockResolvedValue({ items: [list("l1", "Founders")], next_cursor: null });
    archiveLeadList.mockResolvedValue({});
    purgeLeadList.mockResolvedValue({});
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /delete founders permanently/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByRole("textbox"), "Founders");
    await user.click(within(dialog).getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => expect(purgeLeadList).toHaveBeenCalledWith("ws-1", "l1", "Founders"));
    expect(archiveLeadList).toHaveBeenCalledWith("ws-1", "l1", 6);
    expect(archiveLeadList.mock.invocationCallOrder[0]).toBeLessThan(
      purgeLeadList.mock.invocationCallOrder[0],
    );
  });

  it("does not archive again when retrying a delete the server refused", async () => {
    asRole("ADMIN");
    listLeadLists.mockResolvedValue({ items: [list("l1", "Founders")], next_cursor: null });
    archiveLeadList.mockResolvedValue({});
    purgeLeadList
      .mockRejectedValueOnce(new Error("In use"))
      .mockResolvedValueOnce({});
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /delete founders permanently/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByRole("textbox"), "Founders");
    await user.click(within(dialog).getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(purgeLeadList).toHaveBeenCalledTimes(1));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => expect(purgeLeadList).toHaveBeenCalledTimes(2));
    expect(archiveLeadList).toHaveBeenCalledTimes(1);
  });

  it("creates a list by picking leads, then naming it", async () => {
    asRole("MEMBER");
    listLeadLists.mockResolvedValue({ items: [], next_cursor: null });
    listLeads.mockResolvedValue({
      items: [
        { id: "a", first_name: "Ada", last_name: "Lovelace", email: "ada@x.test", company: null },
        { id: "b", first_name: "Grace", last_name: "Hopper", email: "grace@x.test", company: "Navy" },
      ],
      next_cursor: null,
    });
    createLeadList.mockResolvedValue({ id: "new-list", name: "Pioneers" });
    addLeadListMember.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /create list/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByText("Grace Hopper"));
    expect(within(dialog).getByText("1 selected")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Next" }));

    await user.type(within(dialog).getByLabelText(/list name/i), "Pioneers");
    await user.click(within(dialog).getByRole("button", { name: "Create list" }));

    await waitFor(() => expect(createLeadList).toHaveBeenCalledWith("ws-1", "Pioneers"));
    await waitFor(() => expect(addLeadListMember).toHaveBeenCalledWith("ws-1", "new-list", "b"));
    expect(addLeadListMember).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/app/leads/lists/new-list"));
  });

  it("deletes several archived lists at once after typing DELETE, for admins only", async () => {
    search.value = "status=ARCHIVED";
    listLeadLists.mockResolvedValue({
      items: [list("l1", "Old", true), list("l2", "Older", true), list("l3", "Oldest", true)],
      next_cursor: null,
    });
    bulkPurgeLeadLists.mockResolvedValue({ results: [], succeeded: 2, failed: 0 });

    asRole("MANAGER");
    const manager = renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Old" }));
    expect(screen.queryByRole("button", { name: "Delete selected" })).not.toBeInTheDocument();
    manager.unmount();

    asRole("ADMIN");
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("checkbox", { name: "Select Old" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Oldest" }));
    await user.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Delete permanently" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "DELETE");
    await user.click(confirm);

    await waitFor(() =>
      expect(bulkPurgeLeadLists).toHaveBeenCalledWith("ws-1", ["l1", "l3"], "DELETE"),
    );
  });

  it("gives a read-only role no selection or actions", async () => {
    asRole("VIEWER");
    listLeadLists.mockResolvedValue({ items: [list("l1", "Founders")], next_cursor: null });
    renderPage();
    await screen.findByText("Founders");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
