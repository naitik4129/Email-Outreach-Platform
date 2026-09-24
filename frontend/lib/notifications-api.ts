import { apiRequest } from "@/lib/api-client";
import type {
  NotificationItem,
  NotificationPreferences,
} from "@/types/domain";

function workspacePath(workspaceId: string, path: string) {
  return `/api/v1/workspaces/${workspaceId}${path}`;
}

function appendSearch(path: string, params: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export async function listNotifications(
  workspaceId: string,
  params: { unread_only?: boolean; limit?: number; offset?: number } = {},
) {
  const path = appendSearch(
    workspacePath(workspaceId, "/notifications"),
    {
      unread_only:
        params.unread_only !== undefined ? String(params.unread_only) : undefined,
      limit: params.limit?.toString(),
      offset: params.offset?.toString(),
    },
  );
  return (await apiRequest<NotificationItem[]>(path)).data;
}

export async function getUnreadCount(workspaceId: string) {
  return (
    await apiRequest<{ unread_count: number }>(
      workspacePath(workspaceId, "/notifications/unread-count"),
    )
  ).data;
}

export async function markNotificationRead(
  workspaceId: string,
  notificationId: string,
) {
  return (
    await apiRequest<NotificationItem>(
      workspacePath(workspaceId, `/notifications/${notificationId}/read`),
      {
        method: "PATCH",
      },
    )
  ).data;
}

export async function markAllNotificationsRead(workspaceId: string) {
  return (
    await apiRequest<{ marked_count: number }>(
      workspacePath(workspaceId, "/notifications/mark-all-read"),
      {
        method: "POST",
      },
    )
  ).data;
}

export async function getNotificationPreferences() {
  return (
    await apiRequest<NotificationPreferences>("/api/v1/me/notifications/preferences")
  ).data;
}

export async function updateNotificationPreferences(
  payload: Partial<NotificationPreferences>,
) {
  return (
    await apiRequest<NotificationPreferences>(
      "/api/v1/me/notifications/preferences",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}
