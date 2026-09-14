import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace } = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/app/mailboxes",
  useRouter: () => ({ push, replace }),
  useSearchParams: () => new URLSearchParams(),
}));

const { listMailboxes } = vi.hoisted(() => ({
  listMailboxes: vi.fn(),
}));

vi.mock("@/lib/mailboxes-api", () => ({
  listMailboxes,
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));

vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { MailboxesPageClient } from "./mailboxes-page-client";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockWorkspace(roleCode: string) {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-test-1",
    activeWorkspace: { role_code: roleCode },
  });
}

describe("MailboxesPageClient", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an empty read-only page for a viewer without connect button", async () => {
    mockWorkspace("VIEWER");
    listMailboxes.mockResolvedValue([]);

    renderWithClient(<MailboxesPageClient />);

    expect(await screen.findByText("No mailboxes connected yet")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /connect mailbox/i })).not.toBeInTheDocument();
  });

  it("shows mailbox list and connect button for a manager", async () => {
    mockWorkspace("MANAGER");
    listMailboxes.mockResolvedValue([
      {
        id: "mb-1",
        provider: "GMAIL",
        email_address: "outreach@example.com",
        sender_display_name: "Outreach Lead",
        connection_state: "CONNECTED",
        health_state: "HEALTHY",
        policy_state: "ENABLED",
        policy_reason: null,
        circuit_state: "CLOSED",
        sync_state: "IDLE",
        created_at: "2026-01-01T12:00:00Z",
        updated_at: "2026-01-01T12:00:00Z",
      },
    ]);

    renderWithClient(<MailboxesPageClient />);

    expect(await screen.findByText("outreach@example.com")).toBeInTheDocument();
    expect(screen.getByText("Outreach Lead")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /connect mailbox/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage/i })).toHaveAttribute(
      "href",
      "/app/mailboxes/mb-1",
    );
  });

  it("displays Needs Reconnect badge when mailbox is degraded", async () => {
    mockWorkspace("ADMIN");
    listMailboxes.mockResolvedValue([
      {
        id: "mb-2",
        provider: "GMAIL",
        email_address: "degraded@example.com",
        sender_display_name: null,
        connection_state: "RECONNECT_REQUIRED",
        health_state: "DEGRADED",
        policy_state: "ENABLED",
        policy_reason: null,
        circuit_state: "OPEN",
        sync_state: "ERROR",
        created_at: "2026-01-02T12:00:00Z",
        updated_at: "2026-01-02T12:00:00Z",
      },
    ]);

    renderWithClient(<MailboxesPageClient />);

    expect(await screen.findByText("degraded@example.com")).toBeInTheDocument();
    expect(screen.getByText("Needs Reconnect")).toBeInTheDocument();
  });
});
