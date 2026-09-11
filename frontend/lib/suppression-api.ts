import { apiRequest } from "@/lib/api-client";
import type { Suppression, SuppressionPage } from "@/types/domain";

function workspacePath(workspaceId: string, path: string) {
  return `/api/v1/workspaces/${workspaceId}${path}`;
}

function appendSearch(path: string, params: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export async function listSuppressions(
  workspaceId: string,
  params: { limit?: number; cursor?: string | null; q?: string | null } = {},
) {
  const path = appendSearch(workspacePath(workspaceId, "/suppressions"), {
    limit: params.limit?.toString(),
    cursor: params.cursor ?? undefined,
    q: params.q ?? undefined,
  });
  return (await apiRequest<SuppressionPage>(path)).data;
}

export async function getSuppression(workspaceId: string, suppressionId: string) {
  return (
    await apiRequest<Suppression>(
      workspacePath(workspaceId, `/suppressions/${suppressionId}`),
    )
  ).data;
}

export async function createManualSuppression(
  workspaceId: string,
  payload: { email: string },
) {
  return (
    await apiRequest<Suppression>(workspacePath(workspaceId, "/suppressions"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function releaseManualSuppression(
  workspaceId: string,
  suppressionId: string,
  payload: { reason: string },
) {
  return (
    await apiRequest<Suppression>(
      workspacePath(workspaceId, `/suppressions/${suppressionId}/release`),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      },
    )
  ).data;
}
