"use client";

import * as React from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LoadingBlock } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { listSequencePreviewRecipients } from "@/lib/campaigns-api";
import type { SequencePreviewRecipient } from "@/types/domain";

const PAGE_SIZE = 25;

type Props = {
  open: boolean;
  workspaceId: string | null | undefined;
  campaignId: string;
  // Keeps the dialog open and the button busy while the request is in flight.
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (audienceMemberId: string) => void;
};

function displayName(lead: SequencePreviewRecipient) {
  return [lead.first_name, lead.last_name].filter(Boolean).join(" ") || lead.email || "Lead";
}

function matches(lead: SequencePreviewRecipient, needle: string) {
  if (!needle) return true;
  return [lead.first_name, lead.last_name, lead.email, lead.company]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

/** Asks which single lead to write the sample emails for. */
export function SampleLeadPicker({
  open,
  workspaceId,
  campaignId,
  submitting,
  onCancel,
  onConfirm,
}: Props) {
  const [selected, setSelected] = React.useState<string | null>(null);
  const [search, setSearch] = React.useState("");

  const query = useInfiniteQuery({
    queryKey: ["workspace", workspaceId, "campaigns", campaignId, "sample-leads"],
    queryFn: ({ pageParam }) =>
      listSequencePreviewRecipients(workspaceId as string, campaignId, {
        limit: PAGE_SIZE,
        afterOrdinal: pageParam,
      }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: open && Boolean(workspaceId),
    refetchOnWindowFocus: false,
  });

  React.useEffect(() => {
    if (!open) {
      setSelected(null);
      setSearch("");
    }
  }, [open]);

  const leads = React.useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );
  const needle = search.trim().toLowerCase();
  const visible = leads.filter((lead) => matches(lead, needle));
  const total = query.data?.pages[0]?.total ?? null;

  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!submitting) onCancel();
      }}
      title="Choose a lead for the sample"
      align="center"
      className="max-w-lg rounded-xl"
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button
            type="button"
            loading={submitting}
            disabled={!selected}
            onClick={() => selected && onConfirm(selected)}
          >
            Generate sample
          </Button>
        </div>
      }
    >
      <div className="space-y-3 px-4 py-4">
        <p className="text-sm text-slate-600">
          We write your full email sequence for this one lead so you can check the result before
          you approve. Each sample uses AI, so only one lead is generated at a time.
        </p>
        <Input
          type="search"
          aria-label="Search leads"
          placeholder="Search loaded leads by name, email or company"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />

        {query.isLoading ? <LoadingBlock /> : null}
        {query.isError ? (
          <Alert variant="error">
            {query.error instanceof ApiError
              ? query.error.message
              : "We couldn't load the leads. Please try again."}
          </Alert>
        ) : null}
        {!query.isLoading && !query.isError && leads.length === 0 ? (
          <Alert variant="info">
            This campaign has no leads yet. Select and confirm an audience first.
          </Alert>
        ) : null}

        {leads.length > 0 ? (
          <div role="radiogroup" aria-label="Leads" className="max-h-72 space-y-1 overflow-y-auto">
            {visible.map((lead) => (
              <label
                key={lead.audience_member_id}
                className="flex cursor-pointer items-start gap-3 rounded-md border border-slate-200 px-3 py-2 text-sm hover:bg-slate-50 has-[:checked]:border-brand-500 has-[:checked]:bg-brand-50"
              >
                <input
                  type="radio"
                  name="sample-lead"
                  className="mt-1"
                  checked={selected === lead.audience_member_id}
                  onChange={() => setSelected(lead.audience_member_id)}
                />
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-900">
                    {displayName(lead)}
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    {[lead.email, lead.company].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </label>
            ))}
            {visible.length === 0 ? (
              <p className="py-2 text-sm text-slate-500">No loaded lead matches your search.</p>
            ) : null}
          </div>
        ) : null}

        {query.hasNextPage ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            Load more leads{total !== null ? ` (${leads.length} of ${total} shown)` : ""}
          </Button>
        ) : null}
      </div>
    </Dialog>
  );
}
