"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArrowRight, Copy, Pencil, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { canArchiveCampaign } from "@/components/campaigns/campaign-card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { TypeToConfirmDialog } from "@/components/ui/type-to-confirm-dialog";
import {
  archiveCampaign,
  duplicateCampaign,
  getCampaign,
  updateCampaign,
} from "@/lib/campaigns-api";
import { purgeCampaign } from "@/lib/erasure-api";
import { errorMessage } from "@/lib/errors";
import { formatDate, formatDateTime } from "@/lib/format";
import { canDraftCampaign, canEraseData, canExecuteCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignStatus } from "@/types/domain";

const STATUS_HINT: Partial<Record<CampaignStatus, string>> = {
  DRAFT:
    "Finish the audience, sequence, senders and schedule, then start the campaign from the Review tab.",
  SCHEDULED: "This campaign is scheduled. You can pause it from the Review tab.",
  RUNNING: "This campaign is running. You can pause it from the Review tab.",
  PAUSED: "This campaign is paused. You can resume it from the Review tab.",
};

export default function CampaignOverviewPage() {
  const router = useRouter();
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const mayExecute = canExecuteCampaign(activeWorkspace?.role_code);
  const mayErase = canEraseData(activeWorkspace?.role_code);

  const [actionError, setActionError] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [confirmRemoval, setConfirmRemoval] = useState(false);
  const [removalError, setRemovalError] = useState<string | null>(null);
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
      toast("Campaign details saved.");
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
      setConfirmArchive(false);
      toast("Campaign archived.");
    },
    onError: (err) => {
      setConfirmArchive(false);
      setActionError(errorMessage(err));
    },
  });

  const removalMutation = useMutation({
    mutationFn: (phrase: string) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return purgeCampaign(activeWorkspaceId, campaignId, phrase);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "campaigns"],
      });
      setConfirmRemoval(false);
      toast("Campaign deleted.");
      router.push("/app/campaigns");
    },
    onError: (err) => setRemovalError(errorMessage(err)),
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
      toast("Campaign duplicated.");
      router.push(`/app/campaigns/${dup.id}/overview`);
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  if (campaignQuery.isLoading) {
    return (
      <div aria-busy="true" className="max-w-3xl space-y-4">
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-28 w-full rounded-xl" />
      </div>
    );
  }
  if (!campaignQuery.data) return null;
  const campaign = campaignQuery.data;
  const isDraft = campaign.status === "DRAFT";
  const hint = STATUS_HINT[campaign.status];
  const mayArchive = canArchiveCampaign(campaign.status, mayDraft, mayExecute);
  const canRemove = mayErase && campaign.status === "ARCHIVED";

  return (
    <div className="max-w-3xl space-y-5">
      {actionError && <Alert variant="error">{actionError}</Alert>}

      {hint ? (
        <div className="flex flex-col gap-2 rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-brand-900">{hint}</p>
          <Link
            href={`/app/campaigns/${campaignId}/review`}
            className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-brand-700 hover:text-brand-900"
          >
            Go to Review
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </div>
      ) : null}

      <Card>
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
                disabled={!draftName.trim()}
                loading={updateMutation.isPending}
                onClick={() => updateMutation.mutate()}
              >
                Save
              </Button>
            </div>
          </div>
        ) : (
          <>
            <CardHeader
              title="Basics"
              action={
                mayDraft && isDraft ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setDraftName(campaign.name);
                      setDraftDescription(campaign.description ?? "");
                      setIsEditing(true);
                    }}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    Edit
                  </Button>
                ) : null
              }
            />
            <dl className="mt-4 divide-y divide-slate-100 text-sm">
              <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:gap-4">
                <dt className="w-32 shrink-0 text-slate-500">Name</dt>
                <dd className="min-w-0 break-words font-medium text-slate-900">
                  {campaign.name}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:gap-4">
                <dt className="w-32 shrink-0 text-slate-500">Description</dt>
                <dd className="min-w-0 break-words text-slate-900">
                  {campaign.description || (
                    <span className="italic text-slate-400">None</span>
                  )}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:gap-4">
                <dt className="w-32 shrink-0 text-slate-500">Created</dt>
                <dd className="text-slate-900">{formatDate(campaign.created_at)}</dd>
              </div>
              <div className="flex flex-col gap-0.5 py-2.5 sm:flex-row sm:gap-4">
                <dt className="w-32 shrink-0 text-slate-500">Last updated</dt>
                <dd className="text-slate-900">{formatDateTime(campaign.updated_at)}</dd>
              </div>
            </dl>
          </>
        )}
      </Card>

      {mayDraft && (
        <Card>
          <CardHeader
            title="Lifecycle"
            description="Duplicate this campaign to reuse its setup. Archive it when it is finished; a running campaign has to be paused first."
          />
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={duplicateMutation.isPending}
              onClick={() => duplicateMutation.mutate()}
            >
              <Copy className="h-3.5 w-3.5" aria-hidden="true" />
              Duplicate
            </Button>
            {mayArchive && (
              <Button
                variant="outline"
                size="sm"
                className="border-red-200 text-red-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700"
                disabled={archiveMutation.isPending}
                onClick={() => setConfirmArchive(true)}
              >
                <Archive className="h-3.5 w-3.5" aria-hidden="true" />
                Archive
              </Button>
            )}
          </div>
        </Card>
      )}

      {canRemove && (
        <Card className="border-red-200">
          <CardHeader
            title="Delete permanently"
            description="Deletes this campaign, its steps and audience, and every email it sent with their tracking and replies. Your leads, mailboxes and suppression list are not affected."
          />
          <div className="mt-4">
            <Button
              variant="outline"
              size="sm"
              className="border-red-200 text-red-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700"
              onClick={() => {
                setRemovalError(null);
                setConfirmRemoval(true);
              }}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
              Delete campaign…
            </Button>
          </div>
        </Card>
      )}

      {campaign.erased_at ? (
        <Alert variant="info">
          Some of this campaign&apos;s data was removed on{" "}
          {formatDate(campaign.erased_at)}.
        </Alert>
      ) : null}

      <TypeToConfirmDialog
        open={confirmRemoval}
        title={`Delete "${campaign.name}"?`}
        description="This can't be undone. The campaign and every email it sent are removed. Your leads, mailboxes and suppression list are not affected."
        phrase={campaign.name}
        confirmLabel="Delete permanently"
        loading={removalMutation.isPending}
        error={removalError}
        onConfirm={() => removalMutation.mutate(campaign.name)}
        onCancel={() => setConfirmRemoval(false)}
      />

      <ConfirmDialog
        open={confirmArchive}
        title={`Archive "${campaign.name}"?`}
        description={
          isDraft
            ? "It will no longer be editable."
            : "It will stop for good and can't be resumed or edited. Its history and results are kept, and you can duplicate it to run it again."
        }
        confirmLabel="Archive campaign"
        tone="danger"
        loading={archiveMutation.isPending}
        onConfirm={() => archiveMutation.mutate()}
        onCancel={() => setConfirmArchive(false)}
      />
    </div>
  );
}
