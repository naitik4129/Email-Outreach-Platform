import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const { push, replace, refresh, pathnameRef } = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  pathnameRef: { current: "/app/campaigns" },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace, refresh }),
  usePathname: () => pathnameRef.current,
}));

vi.mock("@/lib/supabase/client", () => ({
  clearLocalAuthSession: vi.fn(),
  signOutCurrentBrowser: vi.fn(),
}));

vi.mock("@/lib/notifications-api", () => ({
  getUnreadCount: vi.fn().mockResolvedValue({ unread_count: 0 }),
  listNotifications: vi.fn().mockResolvedValue([]),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
}));

vi.mock("@/lib/workspace-context", () => ({
  useWorkspace: () => ({
    workspaces: [
      {
        workspace_id: "ws-1",
        workspace_name: "QuickICP",
        workspace_status: "ACTIVE",
        membership_id: "m-1",
        role_code: "OWNER",
        membership_version: 1,
      },
    ],
    activeWorkspaceId: "ws-1",
    activeWorkspace: { role_code: "OWNER", workspace_name: "QuickICP" },
    isLoading: false,
    error: null,
    switchWorkspace: vi.fn(),
  }),
  WorkspaceProvider: ({ children }: { children: React.ReactNode }) => children,
}));

import { AppShell } from "@/app/app/app-shell";

function renderShell() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AppShell>content</AppShell>
    </QueryClientProvider>,
  );
}

describe("AppShell navigation", () => {
  afterEach(() => {
    vi.clearAllMocks();
    pathnameRef.current = "/app/campaigns";
  });

  it("keeps every existing destination reachable", () => {
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const hrefs = within(nav)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href"));

    expect(hrefs.sort()).toEqual(
      [
        "/app/dashboard",
        "/app/campaigns",
        "/app/analytics",
        "/app/deliverability",
        "/app/mailboxes",
        "/app/leads",
        "/app/inbox",
        "/app/templates",
        "/app/leads/imports",
        "/app/leads/suppression",
        "/app/team",
      ].sort(),
    );
  });

  it("marks the current section and only that section", () => {
    pathnameRef.current = "/app/campaigns/abc/overview";
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Primary" });

    expect(within(nav).getByRole("link", { name: "Campaigns" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Leads" })).not.toHaveAttribute("aria-current");
  });

  it("highlights Imports, not Leads, on a nested leads route", () => {
    pathnameRef.current = "/app/leads/imports/new";
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Primary" });

    expect(within(nav).getByRole("link", { name: "Imports" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Leads" })).not.toHaveAttribute("aria-current");
  });

  it("still highlights Leads on the leads page itself", () => {
    pathnameRef.current = "/app/leads";
    renderShell();
    const nav = screen.getByRole("navigation", { name: "Primary" });

    expect(within(nav).getByRole("link", { name: "Leads" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("gives small screens a navigation drawer that opens and closes", async () => {
    const user = userEvent.setup();
    renderShell();

    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    expect(within(drawer).getByRole("link", { name: "Campaigns" })).toHaveAttribute(
      "href",
      "/app/campaigns",
    );

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
  });

  it("closes the drawer after choosing a destination", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    await user.click(within(drawer).getByRole("link", { name: "Inbox" }));

    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
  });

  it("offers a skip link to the main content", () => {
    renderShell();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#main-content",
    );
  });
});
