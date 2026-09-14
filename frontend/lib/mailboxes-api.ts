import { apiRequest } from "@/lib/api-client";
import type {
  DisconnectMailboxResult,
  GmailConnectCompleteResult,
  GmailConnectStartResult,
  MailboxDetail,
  MailboxListItem,
  MailboxTestSendResult,
} from "@/types/domain";

function workspacePath(workspaceId: string, path: string) {
  return `/api/v1/workspaces/${workspaceId}${path}`;
}

export async function listMailboxes(workspaceId: string): Promise<MailboxListItem[]> {
  const path = workspacePath(workspaceId, "/mailboxes");
  return (await apiRequest<MailboxListItem[]>(path)).data;
}

export async function getMailbox(workspaceId: string, mailboxId: string): Promise<MailboxDetail> {
  const path = workspacePath(workspaceId, `/mailboxes/${mailboxId}`);
  return (await apiRequest<MailboxDetail>(path)).data;
}

export async function updateMailbox(
  workspaceId: string,
  mailboxId: string,
  payload: {
    sender_display_name?: string | null;
    signature_html?: string | null;
  },
): Promise<MailboxDetail> {
  const path = workspacePath(workspaceId, `/mailboxes/${mailboxId}`);
  return (
    await apiRequest<MailboxDetail>(path, {
      method: "PATCH",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function startGmailOAuth(
  workspaceId: string,
  returnPath: string = "/app/mailboxes",
): Promise<GmailConnectStartResult> {
  const path = workspacePath(workspaceId, "/mailboxes/connect/gmail/start");
  return (
    await apiRequest<GmailConnectStartResult>(path, {
      method: "POST",
      body: JSON.stringify({ return_path: returnPath }),
    })
  ).data;
}

export async function completeGmailOAuth(
  workspaceId: string,
  payload: {
    code: string;
    state: string;
  },
): Promise<GmailConnectCompleteResult> {
  const path = workspacePath(workspaceId, "/mailboxes/connect/gmail/complete");
  return (
    await apiRequest<GmailConnectCompleteResult>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function reconnectGmail(
  workspaceId: string,
  mailboxId: string,
): Promise<GmailConnectStartResult> {
  const path = workspacePath(workspaceId, `/mailboxes/${mailboxId}/reconnect/gmail`);
  return (
    await apiRequest<GmailConnectStartResult>(path, {
      method: "POST",
    })
  ).data;
}

export async function disconnectMailbox(
  workspaceId: string,
  mailboxId: string,
): Promise<DisconnectMailboxResult> {
  const path = workspacePath(workspaceId, `/mailboxes/${mailboxId}/disconnect`);
  return (
    await apiRequest<DisconnectMailboxResult>(path, {
      method: "POST",
    })
  ).data;
}

export async function sendControlledTestEmail(
  workspaceId: string,
  mailboxId: string,
  recipientEmail?: string | null,
): Promise<MailboxTestSendResult> {
  const path = workspacePath(workspaceId, `/mailboxes/${mailboxId}/test-send`);
  return (
    await apiRequest<MailboxTestSendResult>(path, {
      method: "POST",
      body: JSON.stringify({ recipient_email: recipientEmail }),
    })
  ).data;
}
