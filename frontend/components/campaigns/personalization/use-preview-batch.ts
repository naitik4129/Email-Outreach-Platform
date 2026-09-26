"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createPersonalizationPreviews,
  getLatestPersonalizationPreviews,
  newBatchId,
} from "@/lib/personalization-api";

// The latest sample batch of a campaign plus a "generate new samples" action.
// Samples are written by a background worker, so the query polls until every
// sample has finished and then stops.
export function usePreviewBatch(
  workspaceId: string | null | undefined,
  campaignId: string,
  personalizationKey: readonly unknown[],
) {
  const queryClient = useQueryClient();
  const batchKey = [...personalizationKey, "previews", "latest"];

  const query = useQuery({
    queryKey: batchKey,
    queryFn: () => getLatestPersonalizationPreviews(workspaceId as string, campaignId),
    enabled: Boolean(workspaceId && campaignId),
    refetchInterval: (q) => {
      const data = q.state.data;
      return data && !data.complete ? 2500 : false;
    },
  });

  const generate = useMutation({
    mutationFn: () =>
      createPersonalizationPreviews(workspaceId as string, campaignId, {
        batch_id: newBatchId(),
      }),
    onSuccess: (batch) => {
      queryClient.setQueryData(batchKey, batch);
      void queryClient.invalidateQueries({ queryKey: batchKey });
    },
  });

  return { batch: query.data ?? null, query, generate, batchKey };
}
