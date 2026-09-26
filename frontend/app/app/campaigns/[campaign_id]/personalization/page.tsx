"use client";

import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { ApprovalBar } from "@/components/campaigns/personalization/approval-bar";
import { GenerationProgressPanel } from "@/components/campaigns/personalization/generation-progress-panel";
import { ObjectiveForm } from "@/components/campaigns/personalization/objective-form";
import { SamplePreviewPanel } from "@/components/campaigns/personalization/sample-preview-panel";
import { usePreviewBatch } from "@/components/campaigns/personalization/use-preview-batch";
import { Alert } from "@/components/ui/alert";
import { ApiError } from "@/lib/api-client";
import { getCampaign } from "@/lib/campaigns-api";
import {
  approvePersonalization,
  getPersonalization,
  getPersonalizationCapabilities,
  savePersonalization,
} from "@/lib/personalization-api";
import {
  canApprovePersonalization,
  canDraftCampaign,
} from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { PersonalizationConfig } from "@/types/domain";

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export default function CampaignPersonalizationPage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const role = activeWorkspace?.role_code;
  const ready = Boolean(activeWorkspaceId && campaignId);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  const campaignKey = ["workspace", activeWorkspaceId, "campaigns", campaignId];
  const personalizationKey = [...campaignKey, "personalization"];

  const campaignQuery = useQuery({
    queryKey: campaignKey,
    queryFn: () => getCampaign(activeWorkspaceId as string, campaignId),
    enabled: ready,
  });
  const isHyper = campaignQuery.data?.campaign_type === "HYPER_PERSONALIZED";

  const capabilitiesQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "personalization", "capabilities"],
    queryFn: () => getPersonalizationCapabilities(activeWorkspaceId as string),
    enabled: Boolean(activeWorkspaceId),
  });

  const stateQuery = useQuery({
    queryKey: personalizationKey,
    queryFn: () => getPersonalization(activeWorkspaceId as string, campaignId),
    enabled: ready && isHyper,
  });

  const { batch, query: batchQuery, generate } = usePreviewBatch(
    isHyper ? activeWorkspaceId : null,
    campaignId,
    personalizationKey,
  );

  function refreshAfterChange() {
    void queryClient.invalidateQueries({ queryKey: personalizationKey });
    // The header's "items left" count and the review screen come from preflight.
    void queryClient.invalidateQueries({ queryKey: [...campaignKey, "preflight"] });
    void queryClient.invalidateQueries({ queryKey: [...campaignKey, "review"] });
  }

  const saveMutation = useMutation({
    mutationFn: (config: PersonalizationConfig) =>
      savePersonalization(activeWorkspaceId as string, campaignId, {
        config,
        expected_version: stateQuery.data?.config_version ?? null,
      }),
    onSuccess: (state) => {
      setSaveError(null);
      queryClient.setQueryData(personalizationKey, state);
      refreshAfterChange();
    },
    onError: (error) => {
      setSaveError(errorMessage(error));
      if (error instanceof ApiError && error.code === "conflict") {
        void queryClient.invalidateQueries({ queryKey: personalizationKey });
      }
    },
  });

  const approveMutation = useMutation({
    mutationFn: () => {
      if (!batch) throw new Error("No samples to approve");
      return approvePersonalization(activeWorkspaceId as string, campaignId, {
        batch_id: batch.batch_id,
        config_digest: batch.current_digest,
      });
    },
    onSuccess: (state) => {
      setApproveError(null);
      queryClient.setQueryData(personalizationKey, state);
      refreshAfterChange();
    },
    onError: (error) => {
      setApproveError(errorMessage(error));
      void queryClient.invalidateQueries({ queryKey: personalizationKey });
    },
  });

  if (campaignQuery.isLoading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" aria-label="Loading" />
      </div>
    );
  }
  if (campaignQuery.isError || !campaignQuery.data) {
    return <Alert variant="error">{errorMessage(campaignQuery.error)}</Alert>;
  }
  if (!isHyper) {
    return (
      <Alert variant="info">
        This is a standard campaign. Personalization settings only apply to Hyper-Personalized
        campaigns.
      </Alert>
    );
  }
  if (capabilitiesQuery.data && !capabilitiesQuery.data.enabled) {
    return (
      <Alert variant="warning">
        Hyper-personalized campaigns are not enabled for this deployment, so this campaign can&apos;t
        be launched. Ask your administrator to enable them.
      </Alert>
    );
  }

  const campaign = campaignQuery.data;
  const isDraft = campaign.status === "DRAFT";
  const mayDraft = canDraftCampaign(role);
  const readOnly = !isDraft || !mayDraft;
  const state = stateQuery.data;
  const blockedReason = !state?.config
    ? "Save the campaign objective first, then generate samples."
    : null;

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Personalization</h2>
        <p className="text-sm text-slate-500">
          Define the objective once and write a reference email for each step on the Sequence tab.
          For every lead, each email is written from what we know about them, just before it is
          sent, keeping your offer and call to action.
        </p>
      </div>

      {!isDraft ? (
        <Alert variant="info">
          This campaign is {campaign.status.toLowerCase()}, so the objective is read-only.
          Duplicate the campaign to make changes.
        </Alert>
      ) : null}
      {stateQuery.isError ? <Alert variant="error">{errorMessage(stateQuery.error)}</Alert> : null}

      {stateQuery.isLoading ? (
        <div className="flex min-h-[120px] items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" aria-label="Loading" />
        </div>
      ) : state ? (
        <>
          <ObjectiveForm
            initial={state.config}
            readOnly={readOnly}
            saving={saveMutation.isPending}
            error={saveError}
            onSave={(config) => saveMutation.mutate(config)}
          />

          {isDraft ? (
            <>
              <SamplePreviewPanel
                batch={batch}
                loading={batchQuery.isLoading}
                loadError={batchQuery.error}
                canGenerate={mayDraft}
                blockedReason={blockedReason}
                generating={generate.isPending}
                generateError={generate.error}
                onGenerate={() => generate.mutate()}
              />
              <ApprovalBar
                approval={state.approval}
                batch={batch}
                canApprove={canApprovePersonalization(role)}
                readOnly={readOnly}
                approving={approveMutation.isPending}
                error={approveError}
                onApprove={() => approveMutation.mutate()}
              />
            </>
          ) : null}
        </>
      ) : null}

      <GenerationProgressPanel
        workspaceId={activeWorkspaceId ?? ""}
        campaignId={campaignId}
        active={ready && !isDraft}
      />
    </div>
  );
}
