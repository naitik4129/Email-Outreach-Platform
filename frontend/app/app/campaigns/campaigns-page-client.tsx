"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Megaphone, Plus, Search } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { listCampaigns } from "@/lib/campaigns-api";
import { canDraftCampaign } from "@/lib/permissions";
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

function useDebouncedValue(value: string, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function StatusBadge({ status }: { status: CampaignStatus }) {
  const styles: Record<CampaignStatus, string> = {
    DRAFT: "bg-slate-100 text-slate-700",
    SCHEDULED: "bg-blue-100 text-blue-700",
    RUNNING: "bg-emerald-100 text-emerald-700",
    PAUSED: "bg-amber-100 text-amber-700",
    ERROR: "bg-red-100 text-red-700",
    COMPLETED: "bg-indigo-100 text-indigo-700",
    ARCHIVED: "bg-slate-100 text-slate-500",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[status]}`}
    >
      {status}
    </span>
  );
}

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

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Campaigns</h1>
          <p className="text-sm text-slate-500">
            Build and configure outreach campaigns. Nothing sends until activation.
          </p>
        </div>
        {mayDraft && (
          <Button asChild>
            <Link href="/app/campaigns/new">
              <Plus className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Create Campaign
            </Link>
          </Button>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <Input
            placeholder="Search by name..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1 rounded-md border border-slate-200 bg-slate-50 p-0.5 text-xs font-medium text-slate-600">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                const next = new URLSearchParams(params.toString());
                next.set("status", s);
                next.delete("cursor");
                router.push(`${pathname}?${next.toString()}`);
              }}
              className={`rounded px-2.5 py-1 ${
                status === s
                  ? "bg-white font-semibold text-slate-900 shadow-sm"
                  : "hover:text-slate-900"
              }`}
            >
              {s === "ALL" ? "All" : s.charAt(0) + s.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
      </div>

      {campaignsQuery.isLoading ? (
        <div className="flex min-h-[250px] items-center justify-center rounded-lg border border-slate-200 bg-white">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : campaignsQuery.isError ? (
        <Alert variant="error">{errorMessage(campaignsQuery.error)}</Alert>
      ) : campaigns.length === 0 ? (
        <div className="flex min-h-[300px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
            <Megaphone className="h-6 w-6" />
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-900">
            No campaigns yet
          </h2>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            {debouncedSearch || status !== "ALL"
              ? "No campaigns match your filters."
              : "Create a campaign to start configuring an audience, sequence, and schedule."}
          </p>
          {mayDraft && !debouncedSearch && status === "ALL" && (
            <div className="mt-5">
              <Button asChild>
                <Link href="/app/campaigns/new">
                  <Plus className="mr-1.5 h-4 w-4" />
                  Create Campaign
                </Link>
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-6 py-3">Campaign</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3">Updated</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {campaigns.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50/50">
                  <td className="px-6 py-4 font-medium text-slate-900">
                    <Link
                      href={`/app/campaigns/${c.id}/overview`}
                      className="text-indigo-600 hover:text-indigo-800 hover:underline"
                    >
                      {c.name}
                    </Link>
                  </td>
                  <td className="px-6 py-4">
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-slate-500">
                    {formatDate(c.updated_at)}
                  </td>
                  <td className="px-6 py-4 text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" asChild>
                      <Link href={`/app/campaigns/${c.id}/overview`}>
                        {mayDraft ? "Configure" : "View"}
                      </Link>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-6 py-3">
            <div className="text-xs text-slate-500">
              Showing {campaigns.length} campaign{campaigns.length === 1 ? "" : "s"}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!cursor}
                onClick={() => {
                  const next = new URLSearchParams(params.toString());
                  next.delete("cursor");
                  router.push(`${pathname}?${next.toString()}`);
                }}
              >
                First page
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!nextCursor}
                onClick={() => {
                  if (nextCursor) {
                    const next = new URLSearchParams(params.toString());
                    next.set("cursor", nextCursor);
                    router.push(`${pathname}?${next.toString()}`);
                  }
                }}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
