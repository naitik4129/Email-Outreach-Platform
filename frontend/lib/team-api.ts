import { apiRequest } from "@/lib/api-client";
import type {
  RoleCode,
  VerifiedInvitation,
  WorkspaceInvitation,
  WorkspaceMember,
} from "@/types/domain";

function workspacePath(workspaceId: string, path: string) {
  return `/api/v1/workspaces/${workspaceId}${path}`;
}

export async function listTeamMembers(workspaceId: string) {
  return (
    await apiRequest<WorkspaceMember[]>(workspacePath(workspaceId, "/members"))
  ).data;
}

export async function updateMemberRole(
  workspaceId: string,
  memberId: string,
  payload: { role_code: RoleCode; expected_version: number },
) {
  return (
    await apiRequest<WorkspaceMember>(
      workspacePath(workspaceId, `/members/${memberId}`),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}

export async function removeMember(
  workspaceId: string,
  memberId: string,
  payload: { expected_version: number },
) {
  return (
    await apiRequest<void>(workspacePath(workspaceId, `/members/${memberId}`), {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function transferOwnership(
  workspaceId: string,
  payload: {
    target_membership_id: string;
    expected_owner_version: number;
    expected_target_version: number;
  },
) {
  return (
    await apiRequest<{ workspace_id: string; previous_owner_id: string; new_owner_id: string }>(
      workspacePath(workspaceId, "/transfer-ownership"),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}

export async function listInvitations(workspaceId: string) {
  return (
    await apiRequest<WorkspaceInvitation[]>(
      workspacePath(workspaceId, "/invitations"),
    )
  ).data;
}

export async function createInvitation(
  workspaceId: string,
  payload: { email: string; role_code: RoleCode },
) {
  return (
    await apiRequest<{ invitation: WorkspaceInvitation; invite_token?: string }>(
      workspacePath(workspaceId, "/invitations"),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}

export async function resendInvitation(
  workspaceId: string,
  invitationId: string,
) {
  return (
    await apiRequest<WorkspaceInvitation>(
      workspacePath(workspaceId, `/invitations/${invitationId}/resend`),
      {
        method: "POST",
      },
    )
  ).data;
}

export async function revokeInvitation(
  workspaceId: string,
  invitationId: string,
) {
  return (
    await apiRequest<void>(
      workspacePath(workspaceId, `/invitations/${invitationId}/revoke`),
      {
        method: "POST",
      },
    )
  ).data;
}

export async function verifyInvitation(token: string) {
  const query = new URLSearchParams({ token }).toString();
  return (
    await apiRequest<VerifiedInvitation>(`/api/v1/invitations/verify?${query}`)
  ).data;
}

export async function acceptInvitation(token: string) {
  return (
    await apiRequest<{ workspace_id: string; membership_id: string }>(
      "/api/v1/invitations/accept",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      },
    )
  ).data;
}
