import { apiRequest } from "@/lib/api-client";
import type {
  Membership,
  Profile,
  Workspace,
  WorkspaceCreateResult,
  WorkspaceListItem,
} from "@/types/domain";

export async function getMe() {
  return (await apiRequest<Profile>("/api/v1/me")).data;
}

export async function listWorkspaces() {
  return (await apiRequest<WorkspaceListItem[]>("/api/v1/workspaces")).data;
}

export async function createWorkspace(name: string, idempotencyKey: string) {
  return (
    await apiRequest<WorkspaceCreateResult>("/api/v1/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
      idempotencyKey,
    })
  ).data;
}

export async function getWorkspace(workspaceId: string) {
  return (await apiRequest<Workspace>(`/api/v1/workspaces/${workspaceId}`)).data;
}

export async function updateWorkspace(
  workspaceId: string,
  payload: { name?: string; defaults?: Record<string, unknown>; expected_version: number },
) {
  return (
    await apiRequest<Workspace>(`/api/v1/workspaces/${workspaceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function listWorkspaceMembers(workspaceId: string) {
  return (
    await apiRequest<Membership[]>(`/api/v1/workspaces/${workspaceId}/members`)
  ).data;
}
