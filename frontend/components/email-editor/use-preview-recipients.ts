"use client";

import * as React from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import {
  SAMPLE_RECIPIENT,
  type PreviewRecipient,
} from "@/components/email-editor/use-email-preview";
import { ApiError } from "@/lib/api-client";
import { listSequencePreviewRecipients } from "@/lib/campaigns-api";

const PAGE_SIZE = 25;

type Input = {
  workspaceId: string;
  campaignId: string;
  enabled: boolean;
  // The prospect currently shown; when it nears the end of what is loaded the
  // next page is fetched so navigation never dead-ends.
  index: number;
};

export function usePreviewRecipients({ workspaceId, campaignId, enabled, index }: Input) {
  const query = useInfiniteQuery({
    queryKey: ["workspace", workspaceId, "campaigns", campaignId, "preview-recipients"],
    queryFn: ({ pageParam }) =>
      listSequencePreviewRecipients(workspaceId, campaignId, {
        limit: PAGE_SIZE,
        afterOrdinal: pageParam,
      }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
    // Audience snapshots are fixed once captured; no need to refetch on focus.
    refetchOnWindowFocus: false,
  });

  const recipients = React.useMemo<PreviewRecipient[]>(() => {
    const items = query.data?.pages.flatMap((page) => page.items) ?? [];
    return items.map((item) => ({
      id: item.audience_member_id,
      email: item.email ?? "",
      name: [item.first_name, item.last_name].filter(Boolean).join(" "),
      variables: item.variables,
    }));
  }, [query.data]);

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query;
  React.useEffect(() => {
    if (enabled && hasNextPage && !isFetchingNextPage && index >= recipients.length - 2) {
      void fetchNextPage();
    }
  }, [enabled, hasNextPage, isFetchingNextPage, fetchNextPage, index, recipients.length]);

  const usingSample = recipients.length === 0;
  const error = query.isError
    ? query.error instanceof ApiError
      ? `Couldn’t load prospects: ${query.error.message}`
      : "Couldn’t load prospects, showing sample data."
    : null;

  return {
    recipients: usingSample ? [SAMPLE_RECIPIENT] : recipients,
    usingSample,
    total: usingSample ? null : (query.data?.pages[0]?.total ?? null),
    isLoading: query.isLoading,
    error,
  };
}
