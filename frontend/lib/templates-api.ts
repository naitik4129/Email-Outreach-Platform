import { apiRequest } from "@/lib/api-client";
import type {
  TemplateDetail,
  TemplatePage,
  TemplatePreviewResult,
  TemplateVersion,
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

export async function listTemplates(
  workspaceId: string,
  params: {
    limit?: number;
    cursor?: string | null;
    q?: string | null;
    status?: "ACTIVE" | "ARCHIVED" | "ALL";
  } = {},
) {
  const path = appendSearch(workspacePath(workspaceId, "/templates"), {
    limit: params.limit?.toString(),
    cursor: params.cursor ?? undefined,
    q: params.q ?? undefined,
    status: params.status,
  });
  return (await apiRequest<TemplatePage>(path)).data;
}

export async function getTemplate(workspaceId: string, templateId: string) {
  const path = workspacePath(workspaceId, `/templates/${templateId}`);
  return (await apiRequest<TemplateDetail>(path)).data;
}

export async function createTemplate(
  workspaceId: string,
  payload: {
    name: string;
    subject: string;
    body_html: string;
  },
) {
  const path = workspacePath(workspaceId, "/templates");
  return (
    await apiRequest<TemplateDetail>(path, {
      method: "POST",
      body: JSON.stringify({ ...payload, mode: "STANDARD" }),
    })
  ).data;
}

export async function updateTemplate(
  workspaceId: string,
  templateId: string,
  payload: {
    expected_version: number;
    name?: string | null;
    subject?: string | null;
    body_html?: string | null;
  },
) {
  const path = workspacePath(workspaceId, `/templates/${templateId}`);
  return (
    await apiRequest<TemplateDetail>(path, {
      method: "PATCH",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function duplicateTemplate(
  workspaceId: string,
  templateId: string,
  payload: { name?: string | null } = {},
) {
  const path = workspacePath(workspaceId, `/templates/${templateId}/duplicate`);
  return (
    await apiRequest<TemplateDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function archiveTemplate(
  workspaceId: string,
  templateId: string,
  payload: { expected_version: number },
) {
  const path = workspacePath(workspaceId, `/templates/${templateId}/archive`);
  return (
    await apiRequest<TemplateDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function previewTemplate(
  workspaceId: string,
  payload: {
    subject: string;
    body_html: string;
    lead_id?: string | null;
    sample_data?: Record<string, unknown> | null;
  },
) {
  const path = workspacePath(workspaceId, "/templates/preview");
  return (
    await apiRequest<TemplatePreviewResult>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function listTemplateVersions(
  workspaceId: string,
  templateId: string,
) {
  const path = workspacePath(workspaceId, `/templates/${templateId}/versions`);
  return (await apiRequest<TemplateVersion[]>(path)).data;
}
