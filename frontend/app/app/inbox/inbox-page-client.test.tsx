import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

let searchParamsMock = new URLSearchParams();

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/inbox",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => searchParamsMock,
}));

const {
  listConversations,
  getConversation,
  getInboxSyncStatus,
  markConversationRead,
  markConversationUnread,
  archiveConversation,
  unarchiveConversation,
} = vi.hoisted(() => ({
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  getInboxSyncStatus: vi.fn(),
  markConversationRead: vi.fn(),
  markConversationUnread: vi.fn(),
  archiveConversation: vi.fn(),
  unarchiveConversation: vi.fn(),
}));

vi.mock("@/lib/inbox-api", () => ({
  listConversations,
  getConversation,
  getInboxSyncStatus,
  markConversationRead,
  markConversationUnread,
  archiveConversation,
  unarchiveConversation,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { InboxPageClient } from "./inbox-page-client";

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

describe("InboxPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
    searchParamsMock = new URLSearchParams();
  });

  it("renders empty state when there are no conversations", async () => {
    mockWorkspace("MEMBER");
    listConversations.mockResolvedValue({ items: [], next_cursor: null, unread_count: 0 });
    getInboxSyncStatus.mockResolvedValue({ mailboxes: [] });

    renderWithClient(<InboxPageClient />);

    expect(await screen.findByText("Unified Inbox")).toBeInTheDocument();
    expect(await screen.findByText("No conversations")).toBeInTheDocument();
  });

  it("lists conversations with participant, subject, and unread count badge", async () => {
    mockWorkspace("MEMBER");
    listConversations.mockResolvedValue({
      items: [
        {
          id: "conv-1",
          mailbox_id: "mb-1",
          mailbox_address: "sales@company.com",
          mailbox_provider: "GMAIL",
          campaign_id: "camp-1",
          campaign_name: "Outreach 2026",
          subject: "Partnership Inquiry",
          snippet: "Looking forward to hearing from you.",
          latest_activity_at: "2026-09-24T10:00:00Z",
          is_read: false,
          read_at: null,
          archived_at: null,
          participant_email: "alice@acme.com",
          participant_name: "Alice Smith",
          reply_status: "MATCHED",
          message_count: 2,
        },
      ],
      next_cursor: null,
      unread_count: 1,
    });
    getInboxSyncStatus.mockResolvedValue({
      mailboxes: [
        {
          mailbox_id: "mb-1",
          email_address: "sales@company.com",
          provider: "GMAIL",
          connection_status: "CONNECTED",
          sync_scope: "INBOX",
          sync_status: "HEALTHY",
          last_complete_at: "2026-09-24T10:00:00Z",
          failure_count: 0,
        },
      ],
    });

    renderWithClient(<InboxPageClient />);

    expect(await screen.findByText("Alice Smith")).toBeInTheDocument();
    expect(screen.getByText("Partnership Inquiry")).toBeInTheDocument();
    expect(screen.getByText("1 unread")).toBeInTheDocument();
    expect(screen.getByText("sales@company.com")).toBeInTheDocument();
  });

  it("renders conversation detail thread when conversation is selected", async () => {
    mockWorkspace("MEMBER");
    searchParamsMock = new URLSearchParams("cid=conv-1");

    listConversations.mockResolvedValue({
      items: [
        {
          id: "conv-1",
          mailbox_id: "mb-1",
          mailbox_address: "sales@company.com",
          mailbox_provider: "GMAIL",
          campaign_id: null,
          campaign_name: null,
          subject: "Demo Discussion",
          snippet: "Sure, let us talk.",
          latest_activity_at: "2026-09-24T10:00:00Z",
          is_read: false,
          read_at: null,
          archived_at: null,
          participant_email: "bob@acme.com",
          participant_name: "Bob Jones",
          reply_status: "MATCHED",
          message_count: 1,
        },
      ],
      next_cursor: null,
      unread_count: 1,
    });

    getConversation.mockResolvedValue({
      id: "conv-1",
      workspace_id: "ws-1",
      mailbox_id: "mb-1",
      mailbox_address: "sales@company.com",
      mailbox_provider: "GMAIL",
      campaign_id: null,
      campaign_name: null,
      subject: "Demo Discussion",
      latest_activity_at: "2026-09-24T10:00:00Z",
      created_at: "2026-09-24T09:00:00Z",
      is_read: false,
      read_at: null,
      archived_at: null,
      reply_status: "MATCHED",
      participant_email: "bob@acme.com",
      participant_name: "Bob Jones",
      lead_id: null,
      lead_company: null,
      messages: [
        {
          id: "msg-1",
          direction: "INBOUND",
          sender_email: "bob@acme.com",
          sender_name: "Bob Jones",
          recipient_email: "sales@company.com",
          recipient_name: null,
          subject: "Demo Discussion",
          content_text: "Sure, let us talk tomorrow at 2pm.",
          content_html: null,
          timestamp: "2026-09-24T10:00:00Z",
          status: null,
          sequence_step_id: null,
          association_status: "MATCHED",
          classification: "HUMAN_REPLY",
        },
      ],
    });

    getInboxSyncStatus.mockResolvedValue({ mailboxes: [] });

    renderWithClient(<InboxPageClient />);

    expect(await screen.findByText("Sure, let us talk tomorrow at 2pm.")).toBeInTheDocument();
    expect(screen.getAllByText("Bob Jones").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: /mark read/i })).toBeInTheDocument();
    // For MEMBER, Archive button is NOT shown
    expect(screen.queryByRole("button", { name: /^archive$/i })).not.toBeInTheDocument();
  });

  it("shows Archive button for MANAGER role and executes archive mutation", async () => {
    mockWorkspace("MANAGER");
    searchParamsMock = new URLSearchParams("cid=conv-1");

    listConversations.mockResolvedValue({ items: [], next_cursor: null, unread_count: 0 });
    getInboxSyncStatus.mockResolvedValue({ mailboxes: [] });

    getConversation.mockResolvedValue({
      id: "conv-1",
      workspace_id: "ws-1",
      mailbox_id: "mb-1",
      mailbox_address: "sales@company.com",
      mailbox_provider: "GMAIL",
      campaign_id: null,
      campaign_name: null,
      subject: "Old Thread",
      latest_activity_at: "2026-09-24T10:00:00Z",
      created_at: "2026-09-24T09:00:00Z",
      is_read: true,
      read_at: "2026-09-24T10:00:00Z",
      archived_at: null,
      reply_status: "NONE",
      participant_email: "carol@client.com",
      participant_name: "Carol",
      lead_id: null,
      lead_company: null,
      messages: [],
    });

    archiveConversation.mockResolvedValue({
      id: "conv-1",
      is_read: true,
      read_at: "2026-09-24T10:00:00Z",
      archived_at: "2026-09-24T10:05:00Z",
      updated_at: "2026-09-24T10:05:00Z",
    });

    renderWithClient(<InboxPageClient />);

    const archiveBtn = await screen.findByRole("button", { name: /^archive$/i });
    expect(archiveBtn).toBeInTheDocument();

    await userEvent.click(archiveBtn);
    expect(archiveConversation).toHaveBeenCalledWith("ws-1", "conv-1");
  });
});
