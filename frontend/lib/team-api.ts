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
    await apiRequest<WorkspaceMember[]>(workspacePath(workspaceId, "/team"))
  ).data;
}

export async function updateMemberRole(
  workspaceId: string,
  memberId: string,
  payload: { role_code: RoleCode; expected_version: number },
) {
  return (
    await apiRequest<WorkspaceMember>(
      workspacePath(workspaceId, `/team/${memberId}/role`),
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}

export async function removeMember(workspaceId: string, memberId: string) {
  return (
    await apiRequest<{ ok: boolean }>(
      workspacePath(workspaceId, `/team/${memberId}`),
      {
        method: "DELETE",
      },
    )
  ).data;
}

export async function transferOwnership(
  workspaceId: string,
  payload: { new_owner_user_id: string; retain_role: RoleCode },
) {
  return (
    await apiRequest<{ ok: boolean }>(
      workspacePath(workspaceId, "/team/transfer-ownership"),
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
    await apiRequest<{ ok: boolean }>(
      workspacePath(workspaceId, `/invitations/${invitationId}`),
      {
        method: "DELETE",
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
