"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { ApiError } from "@/lib/api-client";
import { getCampaign, getPreflight } from "@/lib/campaigns-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignStatus } from "@/types/domain";

const TABS: { href: string; label: string }[] = [
  { href: "overview", label: "Overview" },
  { href: "audience", label: "Audience" },
  { href: "sequence", label: "Sequence" },
  { href: "senders", label: "Senders" },
  { href: "schedule", label: "Schedule" },
  { href: "review", label: "Review" },
];

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't load this campaign. Please try again.";
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

export default function CampaignLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const { activeWorkspaceId } = useWorkspace();

  const campaignQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getCampaign(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const preflightQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId, "preflight"],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getPreflight(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
    // Preflight is read against authoritative saved state, so re-check it
    // whenever a tab regains focus rather than trusting a stale result
    // while the user edits other tabs.
    refetchOnWindowFocus: true,
  });

  if (campaignQuery.isLoading) {
    return (
      <div className="flex min-h-[300px] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    );
  }

  if (campaignQuery.isError || !campaignQuery.data) {
    return (
      <div className="space-y-4">
        <Alert variant="error">{errorMessage(campaignQuery.error)}</Alert>
        <Link
          href="/app/campaigns"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Campaigns
        </Link>
      </div>
    );
  }

  const campaign = campaignQuery.data;
  const errorCount = preflightQuery.data?.errors.length ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/app/campaigns"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Campaigns
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            {campaign.name}
          </h1>
          <StatusBadge status={campaign.status} />
          {campaign.status === "DRAFT" && errorCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
              <AlertTriangle className="h-3 w-3" />
              {errorCount} item{errorCount === 1 ? "" : "s"} left before this
              campaign is ready
            </span>
          )}
        </div>
        {campaign.description && (
          <p className="mt-1 text-sm text-slate-500">{campaign.description}</p>
        )}
      </div>

      <div className="border-b border-slate-200">
        <nav className="-mb-px flex gap-6 overflow-x-auto">
          {TABS.map((tab) => {
            const href = `/app/campaigns/${campaignId}/${tab.href}`;
            const isActive = pathname?.startsWith(href);
            return (
              <Link
                key={tab.href}
                href={href}
                className={`whitespace-nowrap border-b-2 px-1 py-3 text-sm font-medium ${
                  isActive
                    ? "border-indigo-600 text-indigo-600"
                    : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700"
                }`}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>

      {children}
    </div>
  );
}
