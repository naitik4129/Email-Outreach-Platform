"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { usePreviewBatch } from "@/components/campaigns/personalization/use-preview-batch";
import { ApiError } from "@/lib/api-client";
import {
  approvePersonalization,
  getPersonalization,
  getPersonalizationCapabilities,
  savePersonalization,
} from "@/lib/personalization-api";
import type { PersonalizationConfig } from "@/types/domain";

export function personalizationErrorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

/**
 * Objective, sample previews and approval for one hyper-personalized campaign:
 * everything the old Personalization tab owned, now used by the Sequence tab.
 * `enabled` is false for standard campaigns, so nothing is fetched for them.
 */
export function usePersonalizationState(
  workspaceId: string | null,
  campaignId: string,
  enabled: boolean,
) {
  const queryClient = useQueryClient();
  const [saveError, setSaveError] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  const campaignKey = ["workspace", workspaceId, "campaigns", campaignId];
  const personalizationKey = [...campaignKey, "personalization"];
  const active = Boolean(workspaceId && campaignId && enabled);

  const capabilitiesQuery = useQuery({
    queryKey: ["workspace", workspaceId, "personalization", "capabilities"],
    queryFn: () => getPersonalizationCapabilities(workspaceId as string),
    enabled: Boolean(workspaceId) && enabled,
  });

  const stateQuery = useQuery({
    queryKey: personalizationKey,
    queryFn: () => getPersonalization(workspaceId as string, campaignId),
    enabled: active,
  });

  const preview = usePreviewBatch(active ? workspaceId : null, campaignId, personalizationKey);

  function refreshAfterChange() {
    void queryClient.invalidateQueries({ queryKey: personalizationKey });
    // The header's "items left" count and the review screen come from preflight.
    void queryClient.invalidateQueries({ queryKey: [...campaignKey, "preflight"] });
    void queryClient.invalidateQueries({ queryKey: [...campaignKey, "review"] });
  }

  const saveMutation = useMutation({
    mutationFn: (config: PersonalizationConfig) =>
      savePersonalization(workspaceId as string, campaignId, {
        config,
        expected_version: stateQuery.data?.config_version ?? null,
      }),
    onSuccess: (state) => {
      setSaveError(null);
      queryClient.setQueryData(personalizationKey, state);
      refreshAfterChange();
    },
    onError: (error) => {
      setSaveError(personalizationErrorMessage(error));
      if (error instanceof ApiError && error.code === "conflict") {
        void queryClient.invalidateQueries({ queryKey: personalizationKey });
      }
    },
  });

  const approveMutation = useMutation({
    mutationFn: () => {
      if (!preview.batch) throw new Error("No samples to approve");
      return approvePersonalization(workspaceId as string, campaignId, {
        batch_id: preview.batch.batch_id,
        config_digest: preview.batch.current_digest,
      });
    },
    onSuccess: (state) => {
      setApproveError(null);
      queryClient.setQueryData(personalizationKey, state);
      refreshAfterChange();
    },
    onError: (error) => {
      setApproveError(personalizationErrorMessage(error));
      void queryClient.invalidateQueries({ queryKey: personalizationKey });
    },
  });

  return {
    personalizationKey,
    capabilitiesQuery,
    stateQuery,
    preview,
    saveMutation,
    saveError,
    approveMutation,
    approveError,
    refreshAfterChange,
  };
}
