"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, X } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ProviderBadge } from "@/components/mailboxes/provider-badge";
import { ApiError } from "@/lib/api-client";
import {
  assignCampaignMailbox,
  listCampaignMailboxes,
  reorderCampaignMailboxes,
  unassignCampaignMailbox,
} from "@/lib/campaigns-api";
import { listMailboxes } from "@/lib/mailboxes-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function isUsable(connectionState: string, policyState: string) {
  return connectionState === "CONNECTED" && policyState === "ENABLED";
}

export default function CampaignSendersPage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const [actionError, setActionError] = useState<string | null>(null);

  const assignedKey = [
    "workspace",
    activeWorkspaceId,
    "campaigns",
    campaignId,
    "mailboxes",
  ];

  const assignedQuery = useQuery({
    queryKey: assignedKey,
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? listCampaignMailboxes(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const allMailboxesQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "mailboxes"],
    queryFn: () =>
      activeWorkspaceId
        ? listMailboxes(activeWorkspaceId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: assignedKey });

  const assignMutation = useMutation({
    mutationFn: (mailboxId: string) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return assignCampaignMailbox(activeWorkspaceId, campaignId, mailboxId);
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const unassignMutation = useMutation({
    mutationFn: (mailboxId: string) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return unassignCampaignMailbox(activeWorkspaceId, campaignId, mailboxId);
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const reorderMutation = useMutation({
    mutationFn: (mailboxIds: string[]) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return reorderCampaignMailboxes(activeWorkspaceId, campaignId, mailboxIds);
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const assigned = assignedQuery.data ?? [];
  const assignedIds = new Set(assigned.map((m) => m.mailbox_id));
  const available = (allMailboxesQuery.data ?? []).filter(
    (m) => !assignedIds.has(m.id),
  );

  function move(index: number, direction: -1 | 1) {
    const swap = index + direction;
    if (swap < 0 || swap >= assigned.length) return;
    const ids = assigned.map((m) => m.mailbox_id);
    [ids[index], ids[swap]] = [ids[swap], ids[index]];
    reorderMutation.mutate(ids);
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Senders</h2>
        <p className="text-sm text-slate-500">
          Assign one or more workspace mailboxes. Sending rotation is decided at
          activation, not here.
        </p>
      </div>

      {actionError && <Alert variant="error">{actionError}</Alert>}

      {assignedQuery.isLoading ? (
        <div className="flex min-h-[100px] items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : assigned.length === 0 ? (
        <div className="flex min-h-[100px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          No mailboxes assigned yet.
        </div>
      ) : (
        <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white shadow-sm">
          {assigned.map((mb, i) => (
            <li key={mb.mailbox_id} className="flex items-center justify-between px-4 py-3">
              <div>
                <ProviderBadge provider={mb.provider} />
                <p className="mt-0.5 text-sm text-slate-600">{mb.email_address}</p>
                {!isUsable(mb.connection_state, mb.policy_state) && (
                  <p className="mt-0.5 text-xs text-amber-700">
                    Not currently able to send ({mb.connection_state}/{mb.policy_state})
                  </p>
                )}
              </div>
              {mayDraft && (
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={i === 0 || reorderMutation.isPending}
                    onClick={() => move(i, -1)}
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={i === assigned.length - 1 || reorderMutation.isPending}
                    onClick={() => move(i, 1)}
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-600 hover:text-red-700"
                    disabled={unassignMutation.isPending}
                    onClick={() => unassignMutation.mutate(mb.mailbox_id)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {mayDraft && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-900">Available mailboxes</h3>
          {available.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">
              {allMailboxesQuery.data?.length
                ? "All connected mailboxes are already assigned."
                : "No mailboxes connected yet."}{" "}
              <Link href="/app/mailboxes" className="text-indigo-600 hover:underline">
                Connect a mailbox
              </Link>
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-slate-100">
              {available.map((mb) => (
                <li key={mb.id} className="flex items-center justify-between py-2">
                  <div>
                    <ProviderBadge provider={mb.provider} />
                    <p className="mt-0.5 text-sm text-slate-600">{mb.email_address}</p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={assignMutation.isPending}
                    onClick={() => assignMutation.mutate(mb.id)}
                  >
                    Assign
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
