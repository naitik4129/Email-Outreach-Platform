import { apiRequest } from "@/lib/api-client";
import type { ErasureResult } from "@/types/domain";

// Permanent removal (ADR-0015). Every call is a POST whose body carries the typed
// confirmation, so a name or an email address never appears in a URL. The server
// compares it and re-checks the caller's role; this file only sends the request.

function post(workspaceId: string, path: string, confirm: string) {
  return apiRequest<ErasureResult>(`/api/v1/workspaces/${workspaceId}${path}`, {
    method: "POST",
    body: JSON.stringify({ confirm }),
  }).then((res) => res.data);
}

/** Delete a campaign that was never activated. */
export const purgeCampaign = (ws: string, id: string, confirm: string) =>
  post(ws, `/campaigns/${id}/purge`, confirm);

/** Erase the recipients' personal data from an archived, activated campaign. */
export const eraseCampaign = (ws: string, id: string, confirm: string) =>
  post(ws, `/campaigns/${id}/erase`, confirm);

/** Erase one person everywhere (an active suppression is kept). */
export const eraseLead = (ws: string, id: string, confirm: string) =>
  post(ws, `/leads/${id}/erase`, confirm);

export const purgeTemplate = (ws: string, id: string, confirm: string) =>
  post(ws, `/templates/${id}/purge`, confirm);

export const purgeLeadList = (ws: string, id: string, confirm: string) =>
  post(ws, `/lead-lists/${id}/purge`, confirm);

export const purgeImport = (ws: string, id: string, confirm: string) =>
  post(ws, `/imports/${id}/purge`, confirm);

export const purgeMailbox = (ws: string, id: string, confirm: string) =>
  post(ws, `/mailboxes/${id}/purge`, confirm);

/** Permanently delete the workspace and everything in it. Irreversible. */
export const purgeWorkspace = (ws: string, confirm: string) =>
  post(ws, "/purge", confirm);

/** Imports have no name, so the confirmation is this word. */
export const IMPORT_CONFIRMATION = "DELETE";
