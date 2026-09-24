import { apiRequest } from "@/lib/api-client";
import type {
  ConversationActionResult,
  ConversationDetail,
  ConversationPage,
  InboxFilter,
  InboxSyncStatusResponse,
} from "@/types/domain";

export type ListConversationsParams = {
  limit?: number;
  cursor?: string | null;
  q?: string | null;
  filter?: InboxFilter;
  mailboxId?: string | null;
  campaignId?: string | null;
};

export async function listConversations(
  workspaceId: string,
  params: ListConversationsParams = {},
): Promise<ConversationPage> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.cursor) query.set("cursor", params.cursor);
  if (params.q) query.set("q", params.q);
  if (params.filter && params.filter !== "ALL") query.set("filter", params.filter);
  if (params.mailboxId) query.set("mailbox_id", params.mailboxId);
  if (params.campaignId) query.set("campaign_id", params.campaignId);

  const qs = query.toString();
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations${qs ? `?${qs}` : ""}`;
  const response = await apiRequest<ConversationPage>(endpoint);
  return response.data;
}

export async function getConversation(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationDetail> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations/${conversationId}`;
  const response = await apiRequest<ConversationDetail>(endpoint);
  return response.data;
}

export async function markConversationRead(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationActionResult> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations/${conversationId}/read`;
  const response = await apiRequest<ConversationActionResult>(endpoint, {
    method: "PATCH",
  });
  return response.data;
}

export async function markConversationUnread(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationActionResult> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations/${conversationId}/unread`;
  const response = await apiRequest<ConversationActionResult>(endpoint, {
    method: "PATCH",
  });
  return response.data;
}

export async function archiveConversation(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationActionResult> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations/${conversationId}/archive`;
  const response = await apiRequest<ConversationActionResult>(endpoint, {
    method: "PATCH",
  });
  return response.data;
}

export async function unarchiveConversation(
  workspaceId: string,
  conversationId: string,
): Promise<ConversationActionResult> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/conversations/${conversationId}/unarchive`;
  const response = await apiRequest<ConversationActionResult>(endpoint, {
    method: "PATCH",
  });
  return response.data;
}

export async function getInboxSyncStatus(
  workspaceId: string,
): Promise<InboxSyncStatusResponse> {
  const endpoint = `/api/v1/workspaces/${workspaceId}/inbox/sync-status`;
  const response = await apiRequest<InboxSyncStatusResponse>(endpoint);
  return response.data;
}
