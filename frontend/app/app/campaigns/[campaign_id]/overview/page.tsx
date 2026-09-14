"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  archiveCampaign,
  duplicateCampaign,
  getCampaign,
  updateCampaign,
} from "@/lib/campaigns-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export default function CampaignOverviewPage() {
  const router = useRouter();
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);

  const [actionError, setActionError] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");

  const campaignQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getCampaign(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId],
    });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId || !campaignQuery.data)
        throw new Error("Not ready");
      return updateCampaign(activeWorkspaceId, campaignId, {
        expected_version: campaignQuery.data.version,
        name: draftName,
        description: draftDescription.trim() ? draftDescription : null,
      });
    },
    onSuccess: () => {
      invalidate();
      setIsEditing(false);
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId || !campaignQuery.data)
        throw new Error("Not ready");
      return archiveCampaign(activeWorkspaceId, campaignId, {
        expected_version: campaignQuery.data.version,
      });
    },
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "campaigns"],
      });
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const duplicateMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return duplicateCampaign(activeWorkspaceId, campaignId);
    },
    onSuccess: (dup) => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "campaigns"],
      });
      router.push(`/app/campaigns/${dup.id}/overview`);
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  if (campaignQuery.isLoading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    );
  }
  if (!campaignQuery.data) return null;
  const campaign = campaignQuery.data;
  const isDraft = campaign.status === "DRAFT";

  return (
    <div className="max-w-2xl space-y-6">
      {actionError && <Alert variant="error">{actionError}</Alert>}

      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        {isEditing ? (
          <div className="space-y-4">
            <Field label="Campaign name" required>
              <Input value={draftName} onChange={(e) => setDraftName(e.target.value)} />
            </Field>
            <Field label="Description">
              <Input
                value={draftDescription}
                onChange={(e) => setDraftDescription(e.target.value)}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setIsEditing(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!draftName.trim() || updateMutation.isPending}
                onClick={() => updateMutation.mutate()}
              >
                {updateMutation.isPending && (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                )}
                Save
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">Basics</h2>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-slate-500">Name</dt>
                  <dd className="text-slate-900">{campaign.name}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-slate-500">Description</dt>
                  <dd className="text-slate-900">
                    {campaign.description || (
                      <span className="italic text-slate-400">None</span>
                    )}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-slate-500">Created</dt>
                  <dd className="text-slate-900">
                    {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
                      new Date(campaign.created_at),
                    )}
                  </dd>
                </div>
              </dl>
            </div>
            {mayDraft && isDraft && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setDraftName(campaign.name);
                  setDraftDescription(campaign.description ?? "");
                  setIsEditing(true);
                }}
              >
                Edit
              </Button>
            )}
          </div>
        )}
      </div>

      {mayDraft && (
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-900">Lifecycle</h2>
          <p className="mt-1 text-xs text-slate-500">
            Draft campaigns can be duplicated or archived. Activation isn&apos;t
            available yet in this phase.
          </p>
          <div className="mt-4 flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={duplicateMutation.isPending}
              onClick={() => duplicateMutation.mutate()}
            >
              Duplicate
            </Button>
            {isDraft && (
              <Button
                variant="outline"
                size="sm"
                className="text-red-600 hover:text-red-700"
                disabled={archiveMutation.isPending}
                onClick={() => {
                  if (
                    window.confirm(
                      `Archive "${campaign.name}"? It will no longer be editable.`,
                    )
                  ) {
                    archiveMutation.mutate();
                  }
                }}
              >
                Archive
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
