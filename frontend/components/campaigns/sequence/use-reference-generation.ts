"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as React from "react";

import { ApiError } from "@/lib/api-client";
import { generateReferenceTemplates } from "@/lib/personalization-api";
import type { CampaignSequence } from "@/types/domain";

export type GenerationRequest = {
  scope: "ALL" | "STEP";
  step_id?: string;
  follow_up_count?: number;
};

export type GenerationFailure = {
  message: string;
  code: string | null;
  // Validator codes when the AI's answer failed the checks.
  codes: string[];
  // Consecutive failures; the UI suggests writing by hand after three.
  count: number;
};

/** Drafts (or redrafts) the reference emails through the API, with retry state. */
export function useReferenceGeneration(workspaceId: string | null, campaignId: string) {
  const queryClient = useQueryClient();
  const [failure, setFailure] = React.useState<GenerationFailure | null>(null);
  const [warnings, setWarnings] = React.useState<string[]>([]);
  const [succeeded, setSucceeded] = React.useState(false);
  const failures = React.useRef(0);
  const last = React.useRef<GenerationRequest | null>(null);

  const campaignKey = ["workspace", workspaceId, "campaigns", campaignId];

  const mutation = useMutation({
    mutationFn: (request: GenerationRequest) => {
      last.current = request;
      return generateReferenceTemplates(workspaceId as string, campaignId, request);
    },
    onMutate: () => {
      setFailure(null);
      setSucceeded(false);
    },
    onSuccess: (result) => {
      failures.current = 0;
      setWarnings(result.warnings);
      setSucceeded(true);
      queryClient.setQueryData<CampaignSequence>([...campaignKey, "sequence"], result.sequence);
      // Writing emails changes the approval digest, the header's readiness count and
      // the review screen.
      void queryClient.invalidateQueries({ queryKey: [...campaignKey, "sequence"] });
      void queryClient.invalidateQueries({ queryKey: [...campaignKey, "personalization"] });
      void queryClient.invalidateQueries({ queryKey: [...campaignKey, "preflight"] });
      void queryClient.invalidateQueries({ queryKey: [...campaignKey, "review"] });
    },
    onError: (error) => {
      failures.current += 1;
      const codes =
        error instanceof ApiError && Array.isArray(error.details?.codes)
          ? (error.details?.codes as unknown[]).filter((c): c is string => typeof c === "string")
          : [];
      setFailure({
        message:
          error instanceof ApiError
            ? error.message
            : "We couldn't complete that request. Please try again.",
        code: error instanceof ApiError ? error.code : null,
        codes,
        count: failures.current,
      });
      if (error instanceof ApiError && error.code === "conflict") {
        // Someone changed the emails while we were writing: show what is there now.
        void queryClient.invalidateQueries({ queryKey: [...campaignKey, "sequence"] });
      }
    },
  });

  return {
    generate: (request: GenerationRequest) => mutation.mutate(request),
    retry: () => {
      if (last.current) mutation.mutate(last.current);
    },
    isPending: mutation.isPending,
    // Which request is running, so a single email's button can show its own spinner.
    pendingRequest: mutation.isPending ? mutation.variables : null,
    failure,
    warnings,
    succeeded,
    dismiss: () => {
      setFailure(null);
      setSucceeded(false);
    },
  };
}

export type ReferenceGeneration = ReturnType<typeof useReferenceGeneration>;
