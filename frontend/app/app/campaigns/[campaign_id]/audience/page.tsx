"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Users, XCircle } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  abandonAudience,
  commitAudience,
  getAudienceExclusions,
  getCommittedAudience,
  selectAudience,
} from "@/lib/campaigns-api";
import { listLeadLists, listLeads } from "@/lib/leads-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignAudience } from "@/types/domain";
import { LoadingBlock } from "@/components/ui/skeleton";

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

// Lazily fetches the exclusion-reason breakdown only once expanded, since
// most audience reviews never need it. archived_lead/suppressed are exact
// counts; invalid_address_estimate is a residual the backend computes
// because a lead with no resolvable address never gets a stored row to
// count exactly (see AudienceExclusionBreakdown).
function AudienceExclusionDetails({
  workspaceId,
  campaignId,
  audienceId,
  excludedCount,
}: {
  workspaceId: string;
  campaignId: string;
  audienceId: string;
  excludedCount: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: [
      "workspace",
      workspaceId,
      "campaigns",
      campaignId,
      "audience",
      audienceId,
      "exclusions",
    ],
    queryFn: () => getAudienceExclusions(workspaceId, campaignId, audienceId),
    enabled: expanded,
  });

  return (
    <div className="mt-1">
      <button
        type="button"
        className="text-xs font-medium text-emerald-800 underline-offset-2 hover:underline"
        onClick={() => setExpanded((current) => !current)}
      >
        Why were {excludedCount} lead{excludedCount === 1 ? "" : "s"} excluded?
      </button>
      {expanded ? (
        <div className="mt-1.5 rounded-md bg-white/60 p-2 text-xs text-emerald-900">
          {query.isLoading ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading breakdown...
            </span>
          ) : query.isError ? (
            <span>We couldn&apos;t load the breakdown.</span>
          ) : query.data ? (
            <ul className="space-y-0.5">
              {query.data.archived_lead > 0 ? (
                <li>{query.data.archived_lead} already archived</li>
              ) : null}
              {query.data.suppressed > 0 ? (
                <li>{query.data.suppressed} suppressed (unsubscribed, bounced, or blocked)</li>
              ) : null}
              {query.data.invalid_address_estimate > 0 ? (
                <li>~{query.data.invalid_address_estimate} with no valid email address</li>
              ) : null}
              {query.data.archived_lead === 0 &&
              query.data.suppressed === 0 &&
              query.data.invalid_address_estimate === 0 ? (
                <li>No further detail is available for this revision.</li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function AudienceStatusPanel({
  workspaceId,
  campaignId,
  audience,
  onCommit,
  onAbandon,
  canManage,
  isCommitting,
  isAbandoning,
}: {
  workspaceId: string;
  campaignId: string;
  audience: CampaignAudience;
  onCommit: () => void;
  onAbandon: () => void;
  canManage: boolean;
  isCommitting: boolean;
  isAbandoning: boolean;
}) {
  if (audience.status === "CAPTURING") {
    const total = audience.total_candidates ?? 0;
    const processed = audience.processed_count;
    const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
    return (
      <div className="rounded-lg border border-blue-200 bg-blue-50 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-blue-900">
          <Loader2 className="h-4 w-4 animate-spin" />
          Capturing audience... ({processed} of {total || "?"})
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-blue-100">
          <div
            className="h-full rounded-full bg-blue-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
        {canManage && (
          <div className="mt-3">
            <Button variant="outline" size="sm" disabled={isAbandoning} onClick={onAbandon}>
              Abandon this capture
            </Button>
          </div>
        )}
      </div>
    );
  }

  if (audience.status === "READY") {
    const excludedCount = audience.excluded_count ?? 0;
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
        <div className="flex items-center gap-2 text-sm font-medium text-emerald-900">
          <CheckCircle2 className="h-4 w-4" />
          Audience ready: {audience.accepted_count ?? 0} eligible
          {excludedCount > 0 && (
            <span className="font-normal text-emerald-700">
              ({excludedCount} excluded)
            </span>
          )}
        </div>
        {excludedCount > 0 ? (
          <AudienceExclusionDetails
            workspaceId={workspaceId}
            campaignId={campaignId}
            audienceId={audience.id}
            excludedCount={excludedCount}
          />
        ) : null}
        {!audience.is_committed && canManage && (
          <div className="mt-3">
            <Button size="sm" disabled={isCommitting} onClick={onCommit}>
              {isCommitting && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Use this audience
            </Button>
          </div>
        )}
        {audience.is_committed && (
          <p className="mt-1 text-xs text-emerald-700">
            This is the campaign&apos;s configured audience.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-red-900">
        <XCircle className="h-4 w-4" />
        Audience capture {audience.status.toLowerCase()}
        {audience.error_reason ? `: ${audience.error_reason}` : ""}
      </div>
    </div>
  );
}

export default function CampaignAudiencePage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);

  const [selectedListIds, setSelectedListIds] = useState<Set<string>>(new Set());
  const [leadSearch, setLeadSearch] = useState("");
  const [selectedLeadIds, setSelectedLeadIds] = useState<Set<string>>(new Set());
  const [actionError, setActionError] = useState<string | null>(null);

  const audienceKey = ["workspace", activeWorkspaceId, "campaigns", campaignId, "audience"];

  const audienceQuery = useQuery({
    queryKey: audienceKey,
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getCommittedAudience(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
    refetchInterval: (query) =>
      query.state.data?.status === "CAPTURING" ? 2000 : false,
  });

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", { forCampaign: true }],
    queryFn: () =>
      activeWorkspaceId
        ? listLeadLists(activeWorkspaceId, { limit: 100 })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const leadsQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "leads",
      { forCampaign: true, q: leadSearch },
    ],
    queryFn: () =>
      activeWorkspaceId
        ? listLeads(activeWorkspaceId, { limit: 25, q: leadSearch || null })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && leadSearch.trim().length > 0),
  });

  const invalidateAudience = () =>
    queryClient.invalidateQueries({ queryKey: audienceKey });

  const selectMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return selectAudience(activeWorkspaceId, campaignId, {
        list_ids: Array.from(selectedListIds),
        lead_ids: Array.from(selectedLeadIds),
      });
    },
    onSuccess: () => {
      setSelectedListIds(new Set());
      setSelectedLeadIds(new Set());
      invalidateAudience();
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const commitMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId || !audienceQuery.data)
        throw new Error("Not ready");
      return commitAudience(activeWorkspaceId, campaignId, audienceQuery.data.id);
    },
    onSuccess: invalidateAudience,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const abandonMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId || !audienceQuery.data)
        throw new Error("Not ready");
      return abandonAudience(activeWorkspaceId, campaignId, audienceQuery.data.id);
    },
    onSuccess: invalidateAudience,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const audience = audienceQuery.data;
  const canSelectNew =
    mayDraft && (!audience || audience.status !== "CAPTURING");

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Audience</h2>
        <p className="text-sm text-slate-500">
          Select the leads or lists you intend to include. Suppression and
          eligibility are re-checked again before anything sends.
        </p>
      </div>

      {actionError && <Alert variant="error">{actionError}</Alert>}

      {audienceQuery.isLoading ? (
        <LoadingBlock />
      ) : audience && activeWorkspaceId ? (
        <AudienceStatusPanel
          workspaceId={activeWorkspaceId}
          campaignId={campaignId}
          audience={audience}
          onCommit={() => commitMutation.mutate()}
          onAbandon={() => abandonMutation.mutate()}
          canManage={mayDraft}
          isCommitting={commitMutation.isPending}
          isAbandoning={abandonMutation.isPending}
        />
      ) : (
        <div className="flex min-h-[80px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          <Users className="mb-2 h-5 w-5 text-slate-400" />
          No audience selected yet.
        </div>
      )}

      {canSelectNew && (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <h3 className="text-sm font-semibold text-slate-900">
            {audience ? "Select a new audience" : "Select audience"}
          </h3>
          <p className="mt-1 text-xs text-slate-500">
            Choosing a new audience creates a new capture revision; it doesn&apos;t
            change the currently committed audience until you commit it.
          </p>

          <div className="mt-4 space-y-4">
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Lead lists
              </h4>
              <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                {listsQuery.data?.items.length ? (
                  listsQuery.data.items.map((list) => (
                    <label
                      key={list.id}
                      className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedListIds.has(list.id)}
                        onChange={(e) => {
                          const next = new Set(selectedListIds);
                          if (e.target.checked) next.add(list.id);
                          else next.delete(list.id);
                          setSelectedListIds(next);
                        }}
                      />
                      {list.name}
                      <span className="text-xs text-slate-400">
                        ({list.member_count})
                      </span>
                    </label>
                  ))
                ) : (
                  <p className="px-2 py-1 text-xs text-slate-400">No lists yet.</p>
                )}
              </div>
            </div>

            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Individual leads
              </h4>
              <Input
                className="mt-2"
                placeholder="Search leads by name, email, or company..."
                value={leadSearch}
                onChange={(e) => setLeadSearch(e.target.value)}
              />
              {leadSearch.trim() && (
                <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                  {leadsQuery.data?.items.length ? (
                    leadsQuery.data.items.map((lead) => (
                      <label
                        key={lead.id}
                        className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
                      >
                        <input
                          type="checkbox"
                          checked={selectedLeadIds.has(lead.id)}
                          onChange={(e) => {
                            const next = new Set(selectedLeadIds);
                            if (e.target.checked) next.add(lead.id);
                            else next.delete(lead.id);
                            setSelectedLeadIds(next);
                          }}
                        />
                        {lead.first_name} {lead.last_name} &mdash; {lead.email}
                      </label>
                    ))
                  ) : (
                    <p className="px-2 py-1 text-xs text-slate-400">
                      No matching leads.
                    </p>
                  )}
                </div>
              )}
              {selectedLeadIds.size > 0 && (
                <p className="mt-1 text-xs text-slate-500">
                  {selectedLeadIds.size} lead{selectedLeadIds.size === 1 ? "" : "s"}{" "}
                  selected individually.
                </p>
              )}
            </div>

            <div className="flex justify-end">
              <Button
                disabled={
                  (selectedListIds.size === 0 && selectedLeadIds.size === 0) ||
                  selectMutation.isPending
                }
                onClick={() => selectMutation.mutate()}
              >
                {selectMutation.isPending && (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                )}
                Capture audience
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
