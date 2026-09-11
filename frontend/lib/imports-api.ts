import { apiRequest } from "@/lib/api-client";
import type {
  ImportCreateIn,
  ImportJob,
  ImportJobPage,
  ImportRowResultPage,
  ImportUploadOut,
} from "@/types/domain";

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

export async function uploadImportFile(workspaceId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);

  return (
    await apiRequest<ImportUploadOut>(workspacePath(workspaceId, "/imports/upload"), {
      method: "POST",
      body: formData,
    })
  ).data;
}

export async function createImport(workspaceId: string, payload: ImportCreateIn) {
  return (
    await apiRequest<ImportJob>(workspacePath(workspaceId, "/imports"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function listImports(
  workspaceId: string,
  params: { limit?: number; cursor?: string | null } = {},
) {
  const path = appendSearch(workspacePath(workspaceId, "/imports"), {
    limit: params.limit?.toString(),
    cursor: params.cursor ?? undefined,
  });
  return (await apiRequest<ImportJobPage>(path)).data;
}

export async function getImport(workspaceId: string, importId: string) {
  return (
    await apiRequest<ImportJob>(workspacePath(workspaceId, `/imports/${importId}`))
  ).data;
}

export async function listImportRowResults(
  workspaceId: string,
  importId: string,
  params: { limit?: number; cursor?: string | null } = {},
) {
  const path = appendSearch(workspacePath(workspaceId, `/imports/${importId}/results`), {
    limit: params.limit?.toString(),
    cursor: params.cursor ?? undefined,
  });
  return (await apiRequest<ImportRowResultPage>(path)).data;
}
