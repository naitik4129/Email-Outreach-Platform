import { apiRequest } from "@/lib/api-client";
import type { WorkspaceUsage } from "@/types/domain";

export async function getWorkspaceUsage(workspaceId: string) {
  return (
    await apiRequest<WorkspaceUsage>(`/api/v1/workspaces/${workspaceId}/usage`)
  ).data;
}
