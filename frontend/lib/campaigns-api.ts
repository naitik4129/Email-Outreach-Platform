import { apiRequest } from "@/lib/api-client";
import type {
  CampaignAudience,
  CampaignDetail,
  CampaignMailbox,
  CampaignPage,
  CampaignPlanning,
  CampaignReview,
  CampaignSequence,
  CampaignSettings,
  PreflightResult,
  SequenceStep,
  SequenceStepKind,
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

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// --- Campaign CRUD ---

export async function listCampaigns(
  workspaceId: string,
  params: {
    limit?: number;
    cursor?: string | null;
    q?: string | null;
    status?: string | null;
  } = {},
) {
  const path = appendSearch(workspacePath(workspaceId, "/campaigns"), {
    limit: params.limit?.toString(),
    cursor: params.cursor ?? undefined,
    q: params.q ?? undefined,
    status: params.status ?? undefined,
  });
  return (await apiRequest<CampaignPage>(path)).data;
}

export async function createCampaign(
  workspaceId: string,
  payload: { name: string; description?: string | null },
) {
  const path = workspacePath(workspaceId, "/campaigns");
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
      idempotencyKey: newIdempotencyKey(),
    })
  ).data;
}

export async function getCampaign(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}`);
  return (await apiRequest<CampaignDetail>(path)).data;
}

export async function updateCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { expected_version: number; name?: string; description?: string | null },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "PATCH",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function archiveCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { expected_version: number },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/archive`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function duplicateCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { name?: string | null } = {},
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/duplicate`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

// --- Sequence ---

export async function getSequence(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/sequence`);
  return (await apiRequest<CampaignSequence>(path)).data;
}

export async function addSequenceStep(
  workspaceId: string,
  campaignId: string,
  payload: {
    kind: SequenceStepKind;
    position: number;
    email_subject?: string | null;
    email_body_html?: string | null;
    source_template_version_id?: string | null;
    wait_duration_minutes?: number | null;
  },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/sequence/steps`);
  return (
    await apiRequest<SequenceStep>(path, { method: "POST", body: JSON.stringify(payload) })
  ).data;
}

export async function updateSequenceStep(
  workspaceId: string,
  campaignId: string,
  stepId: string,
  payload: {
    expected_version: number;
    email_subject?: string | null;
    email_body_html?: string | null;
    source_template_version_id?: string | null;
    wait_duration_minutes?: number | null;
  },
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/sequence/steps/${stepId}`,
  );
  return (
    await apiRequest<SequenceStep>(path, { method: "PATCH", body: JSON.stringify(payload) })
  ).data;
}

export async function deleteSequenceStep(
  workspaceId: string,
  campaignId: string,
  stepId: string,
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/sequence/steps/${stepId}`,
  );
  await apiRequest<void>(path, { method: "DELETE" });
}

export async function reorderSequenceSteps(
  workspaceId: string,
  campaignId: string,
  steps: { step_id: string; position: number }[],
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/sequence/steps/reorder`,
  );
  return (
    await apiRequest<CampaignSequence>(path, {
      method: "POST",
      body: JSON.stringify({ steps }),
    })
  ).data;
}

// --- Mailboxes ---

export async function listCampaignMailboxes(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/mailboxes`);
  return (await apiRequest<CampaignMailbox[]>(path)).data;
}

export async function assignCampaignMailbox(
  workspaceId: string,
  campaignId: string,
  mailboxId: string,
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/mailboxes`);
  return (
    await apiRequest<CampaignMailbox[]>(path, {
      method: "POST",
      body: JSON.stringify({ mailbox_id: mailboxId }),
    })
  ).data;
}

export async function unassignCampaignMailbox(
  workspaceId: string,
  campaignId: string,
  mailboxId: string,
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/mailboxes/${mailboxId}`,
  );
  return (await apiRequest<CampaignMailbox[]>(path, { method: "DELETE" })).data;
}

export async function reorderCampaignMailboxes(
  workspaceId: string,
  campaignId: string,
  mailboxIds: string[],
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/mailboxes/reorder`);
  return (
    await apiRequest<CampaignMailbox[]>(path, {
      method: "POST",
      body: JSON.stringify({ mailbox_ids: mailboxIds }),
    })
  ).data;
}

// --- Schedule / settings ---

export async function listCampaignSettings(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/settings`);
  return (await apiRequest<CampaignSettings[]>(path)).data;
}

export async function createCampaignSettings(
  workspaceId: string,
  campaignId: string,
  payload: {
    timezone: string;
    weekdays: number[];
    window_start_local: string;
    window_end_local: string;
    daily_limit?: number | null;
  },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/settings`);
  return (
    await apiRequest<CampaignSettings>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

// --- Audience ---

export async function getCommittedAudience(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/audience`);
  return (await apiRequest<CampaignAudience | null>(path)).data;
}

export async function selectAudience(
  workspaceId: string,
  campaignId: string,
  payload: { lead_ids: string[]; list_ids: string[] },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/audience/select`);
  return (
    await apiRequest<CampaignAudience>(path, {
      method: "POST",
      body: JSON.stringify(payload),
      idempotencyKey: newIdempotencyKey(),
    })
  ).data;
}

export async function getAudienceCaptureStatus(
  workspaceId: string,
  campaignId: string,
  audienceId: string,
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/audience/${audienceId}`,
  );
  return (await apiRequest<CampaignAudience>(path)).data;
}

export async function commitAudience(
  workspaceId: string,
  campaignId: string,
  audienceId: string,
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/audience/${audienceId}/commit`,
  );
  return (await apiRequest<CampaignAudience>(path, { method: "POST" })).data;
}

export async function abandonAudience(
  workspaceId: string,
  campaignId: string,
  audienceId: string,
) {
  const path = workspacePath(
    workspaceId,
    `/campaigns/${campaignId}/audience/${audienceId}/abandon`,
  );
  await apiRequest<void>(path, { method: "POST" });
}

// --- Activation / planning ---

export async function activateCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { expected_version: number; start_at?: string | null },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/activate`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
      idempotencyKey: newIdempotencyKey(),
    })
  ).data;
}

export async function pauseCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { expected_version: number },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/pause`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function resumeCampaign(
  workspaceId: string,
  campaignId: string,
  payload: { expected_version: number },
) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/resume`);
  return (
    await apiRequest<CampaignDetail>(path, {
      method: "POST",
      body: JSON.stringify(payload),
    })
  ).data;
}

export async function getCampaignPlanning(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/planning`);
  return (await apiRequest<CampaignPlanning>(path)).data;
}

// --- Preflight / Review ---

export async function getPreflight(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/preflight`);
  return (await apiRequest<PreflightResult>(path)).data;
}

export async function getReview(workspaceId: string, campaignId: string) {
  const path = workspacePath(workspaceId, `/campaigns/${campaignId}/review`);
  return (await apiRequest<CampaignReview>(path)).data;
}
