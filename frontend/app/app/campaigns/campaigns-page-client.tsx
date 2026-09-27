"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Megaphone, Plus, Search } from "lucide-react";

import {
  CampaignCard,
  CampaignCardSkeleton,
} from "@/components/campaigns/campaign-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { CursorPagination } from "@/components/ui/pagination";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { PageHeader } from "@/components/ui/page-header";
import { listCampaigns } from "@/lib/campaigns-api";
import { errorMessage } from "@/lib/errors";
import { canDraftCampaign } from "@/lib/permissions";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignStatus } from "@/types/domain";

const PAGE_SIZE = 25;
const STATUS_FILTERS: (CampaignStatus | "ALL")[] = [
  "ALL",
  "DRAFT",
  "SCHEDULED",
  "RUNNING",
  "PAUSED",
  "COMPLETED",
  "ARCHIVED",
];

const STATUS_OPTIONS = STATUS_FILTERS.map((s) => ({
  value: s,
  label: s === "ALL" ? "All" : s.charAt(0) + s.slice(1).toLowerCase(),
}));

export function CampaignsPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [searchText, setSearchText] = useState(params.get("q") ?? "");

  const debouncedSearch = useDebouncedValue(searchText, 350);
  const cursor = params.get("cursor");
  const status = (params.get("status") as CampaignStatus | "ALL" | null) ?? "ALL";
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);

  useEffect(() => {
    const next = new URLSearchParams(params.toString());
    if (debouncedSearch) next.set("q", debouncedSearch);
    else next.delete("q");
    next.delete("cursor");
    const href = `${pathname}?${next.toString()}`;
    if ((params.get("q") ?? "") !== debouncedSearch) {
      router.replace(href.endsWith("?") ? pathname : href);
    }
  }, [debouncedSearch, params, pathname, router]);

  const campaignsQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "campaigns",
      { cursor, q: params.get("q"), status },
    ],
    queryFn: () =>
      activeWorkspaceId
        ? listCampaigns(activeWorkspaceId, {
            limit: PAGE_SIZE,
            cursor,
            q: params.get("q"),
            status: status === "ALL" ? null : status,
          })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const campaigns = campaignsQuery.data?.items ?? [];
  const nextCursor = campaignsQuery.data?.next_cursor ?? null;
  const isFiltered = Boolean(debouncedSearch) || status !== "ALL";

  const createButton = mayDraft ? (
    <Button asChild>
      <Link href="/app/campaigns/new">
        <Plus className="h-4 w-4" aria-hidden="true" />
        Create Campaign
      </Link>
    </Button>
  ) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Campaigns"
        description="Build and configure outreach campaigns, then track how they perform. Nothing sends until activation."
        actions={createButton}
      />

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="relative w-full lg:max-w-sm">
          <Search
            className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
            aria-hidden="true"
          />
          <Input
            type="search"
            aria-label="Search campaigns by name"
            placeholder="Search by name..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-9"
          />
        </div>
        <SegmentedControl
          label="Filter by status"
          options={STATUS_OPTIONS}
          value={status}
          onChange={(s) => {
            const next = new URLSearchParams(params.toString());
            next.set("status", s);
            next.delete("cursor");
            router.push(`${pathname}?${next.toString()}`);
          }}
        />
      </div>

      {campaignsQuery.isLoading ? (
        <div
          aria-busy="true"
          className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
        >
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <CampaignCardSkeleton key={i} />
          ))}
        </div>
      ) : campaignsQuery.isError ? (
        <div className="space-y-3">
          <Alert variant="error">{errorMessage(campaignsQuery.error)}</Alert>
          <Button variant="outline" size="sm" onClick={() => campaignsQuery.refetch()}>
            Try again
          </Button>
        </div>
      ) : campaigns.length === 0 ? (
        <EmptyState
          icon={<Megaphone />}
          title={isFiltered ? "No campaigns match your filters" : "No campaigns yet"}
          description={
            isFiltered
              ? "Try a different search or status, or clear the filters to see every campaign."
              : "A campaign pairs an audience with an email sequence, sender mailboxes and a schedule. Nothing sends until you activate it."
          }
          action={
            !isFiltered ? (
              createButton
            ) : (
              <Button
                variant="outline"
                onClick={() => {
                  setSearchText("");
                  router.push(pathname);
                }}
              >
                Clear filters
              </Button>
            )
          }
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {activeWorkspaceId
              ? campaigns.map((c) => (
                  <CampaignCard
                    key={c.id}
                    campaign={c}
                    workspaceId={activeWorkspaceId}
                    mayDraft={mayDraft}
                  />
                ))
              : null}
            {mayDraft && !isFiltered && cursor === null ? (
              <Link
                href="/app/campaigns/new"
                className="group flex min-h-[220px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 bg-white/60 p-6 text-center transition-colors hover:border-brand-400 hover:bg-brand-50/40"
              >
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-brand-50 text-brand-600 transition-colors group-hover:bg-brand-100">
                  <Plus className="h-5 w-5" aria-hidden="true" />
                </span>
                <span className="text-sm font-semibold text-slate-900">
                  Create Campaign
                </span>
                <span className="max-w-[16rem] text-xs text-slate-500">
                  Pick an audience, write the sequence and choose your senders.
                </span>
              </Link>
            ) : null}
          </div>

          <CursorPagination
            className="rounded-xl border bg-white shadow-card"
            summary={`Showing ${campaigns.length} campaign${campaigns.length === 1 ? "" : "s"}`}
            canGoFirst={Boolean(cursor)}
            canGoNext={Boolean(nextCursor)}
            onFirst={() => {
              const next = new URLSearchParams(params.toString());
              next.delete("cursor");
              router.push(`${pathname}?${next.toString()}`);
            }}
            onNext={() => {
              if (nextCursor) {
                const next = new URLSearchParams(params.toString());
                next.set("cursor", nextCursor);
                router.push(`${pathname}?${next.toString()}`);
              }
            }}
          />
        </>
      )}
    </div>
  );
}
