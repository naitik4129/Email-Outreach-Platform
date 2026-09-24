import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useWorkspace } = vi.hoisted(() => ({
  useWorkspace: vi.fn(),
}));

vi.mock("@/lib/workspace-context", () => ({
  useWorkspace,
}));

const mockNotificationsApi = vi.hoisted(() => ({
  listNotifications: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
}));

vi.mock("@/lib/notifications-api", () => mockNotificationsApi);

import NotificationsPage from "@/app/app/notifications/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationsPage />
    </QueryClientProvider>,
  );
}

describe("NotificationsPage", () => {
  const user = userEvent.setup();

  beforeEach(() => {
    useWorkspace.mockReturnValue({
      activeWorkspaceId: "ws-1",
    });

    mockNotificationsApi.listNotifications.mockResolvedValue([
      {
        id: "notif-1",
        workspace_id: "ws-1",
        user_id: "u-1",
        category: "safety",
        title: "Complaint threshold alert",
        body: "Your mailbox exceeded complaint rate threshold.",
        data: {},
        is_read: false,
        read_at: null,
        created_at: "2026-03-01T12:00:00Z",
      },
      {
        id: "notif-2",
        workspace_id: "ws-1",
        user_id: "u-1",
        category: "campaign",
        title: "Campaign Sending Completed",
        body: "Spring Outreach finished reaching 500 leads.",
        data: {},
        is_read: true,
        read_at: "2026-03-01T12:30:00Z",
        created_at: "2026-03-01T12:00:00Z",
      },
    ]);

    mockNotificationsApi.markAllNotificationsRead.mockResolvedValue({
      marked_count: 1,
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders notifications list and shows unread badge", async () => {
    renderPage();

    expect(await screen.findByText("Complaint threshold alert")).toBeInTheDocument();
    expect(screen.getByText("Campaign Sending Completed")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument(); // Unread tab count
  });

  it("calls mark all read when button is clicked", async () => {
    renderPage();

    const markAllBtn = await screen.findByRole("button", {
      name: /mark all as read/i,
    });
    await user.click(markAllBtn);

    await waitFor(() => {
      expect(mockNotificationsApi.markAllNotificationsRead).toHaveBeenCalledWith("ws-1");
    });
  });
});
