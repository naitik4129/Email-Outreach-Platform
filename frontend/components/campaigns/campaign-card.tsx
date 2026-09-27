"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BarChart3, Copy, Eye, Send, Sparkles } from "lucide-react";

import { CampaignTypeBadge } from "@/components/campaigns/campaign-type-badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CampaignStatusBadge,
  campaignStatusMeta,
} from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { getCampaignAnalytics } from "@/lib/analytics-api";
import { archiveCampaign, duplicateCampaign } from "@/lib/campaigns-api";
import { errorMessage } from "@/lib/errors";
import { formatNumber, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CampaignListItem } from "@/types/domain";

function Metric({ label, value }: { label: string; value: number }) {
  return (
    // dt/dd stay in semantic order; the value is shown above its label.
    <div className="flex min-w-0 flex-col-reverse">
      <dt className="truncate text-xs text-slate-500">{label}</dt>
      <dd className="text-base font-semibold tabular-nums text-slate-900">
        {formatNumber(value)}
      </dd>
    </div>
  );
}

function CampaignMetrics({
  workspaceId,
  campaign,
}: {
  workspaceId: string;
  campaign: CampaignListItem;
}) {
  // Same key the campaign Analytics tab uses, so the cache is shared. Drafts
  // have never sent, so they skip the request entirely.
  const analyticsQuery = useQuery({
    queryKey: ["workspace", workspaceId, "campaigns", campaign.id, "analytics"],
    queryFn: () => getCampaignAnalytics(workspaceId, campaign.id),
    enabled: campaign.status !== "DRAFT",
    staleTime: 60_000,
  });

  if (campaign.status === "DRAFT") {
    return (
      <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-500">
        Not launched yet. Finish setup in the campaign to start sending.
      </p>
    );
  }

  if (analyticsQuery.isLoading) {
    return (
      <div aria-busy="true" className="space-y-3 rounded-lg bg-slate-50 px-3 py-3">
        <div className="grid grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="space-y-1.5">
              <Skeleton className="h-4 w-8" />
              <Skeleton className="h-3 w-12" />
            </div>
          ))}
        </div>
        <Skeleton className="h-1.5 w-full" />
      </div>
    );
  }

  const data = analyticsQuery.data;
  if (!data) {
    return (
      <p className="rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-slate-500">
        Performance metrics are unavailable right now.
      </p>
    );
  }

  const opensTracked = data.open_tracking_supported !== false;
  const rate = opensTracked ? data.open_rate ?? 0 : data.reply_rate;
  const rateLabel = opensTracked ? "open rate" : "reply rate";

  return (
    <div className="space-y-3 rounded-lg bg-slate-50 px-3 py-3">
      <dl className={cn("grid gap-3", opensTracked ? "grid-cols-4" : "grid-cols-3")}>
        <Metric label="Sent" value={data.sent} />
        {opensTracked ? <Metric label="Opened" value={data.opened ?? 0} /> : null}
        <Metric label="Replies" value={data.replied} />
        <Metric label="Bounced" value={data.bounced} />
      </dl>
      <div>
        <div
          role="progressbar"
          aria-label={`${rateLabel} ${rate.toFixed(1)}%`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.min(100, Math.round(rate))}
          className="h-1.5 overflow-hidden rounded-full bg-slate-200"
        >
          <div
            className="h-full rounded-full bg-brand-500 transition-[width] duration-500"
            style={{ width: `${Math.min(100, rate)}%` }}
          />
        </div>
        <p className="mt-1.5 text-xs text-slate-500">
          <span className="font-medium text-slate-700">{rate.toFixed(1)}%</span> {rateLabel}
        </p>
      </div>
    </div>
  );
}

export function CampaignCard({
  campaign,
  workspaceId,
  mayDraft,
}: {
  campaign: CampaignListItem;
  workspaceId: string;
  mayDraft: boolean;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const [confirmArchive, setConfirmArchive] = useState(false);
  const overviewHref = `/app/campaigns/${campaign.id}/overview`;
  const isHyper = campaign.campaign_type === "HYPER_PERSONALIZED";

  const invalidateList = () =>
    queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "campaigns"] });

  const duplicateMutation = useMutation({
    mutationFn: () => duplicateCampaign(workspaceId, campaign.id),
    onSuccess: (copy) => {
      invalidateList();
      toast(`Duplicated as “${copy.name}”.`);
      router.push(`/app/campaigns/${copy.id}/overview`);
    },
    onError: (err) => toast(errorMessage(err), "error"),
  });

  const archiveMutation = useMutation({
    mutationFn: () =>
      archiveCampaign(workspaceId, campaign.id, { expected_version: campaign.version }),
    onSuccess: () => {
      invalidateList();
      setConfirmArchive(false);
      toast(`Archived “${campaign.name}”.`);
    },
    onError: (err) => {
      setConfirmArchive(false);
      toast(errorMessage(err), "error");
    },
  });

  const menuItems = [
    { label: "Overview", href: overviewHref, icon: <Eye /> },
    {
      label: "Analytics",
      href: `/app/campaigns/${campaign.id}/analytics`,
      icon: <BarChart3 />,
    },
    ...(mayDraft
      ? [
          {
            label: "Duplicate",
            icon: <Copy />,
            disabled: duplicateMutation.isPending,
            onSelect: () => duplicateMutation.mutate(),
          },
        ]
      : []),
    ...(mayDraft && campaign.status === "DRAFT"
      ? [
          {
            label: "Archive",
            icon: <Archive />,
            tone: "danger" as const,
            onSelect: () => setConfirmArchive(true),
          },
        ]
      : []),
  ];

  return (
    <article
      aria-label={campaign.name}
      className="group relative flex flex-col rounded-xl border border-slate-200 bg-white shadow-card transition-[box-shadow,border-color] duration-150 hover:border-slate-300 hover:shadow-card-hover focus-within:border-brand-300"
    >
      <div
        aria-hidden="true"
        className={cn("h-1 rounded-t-xl", (campaignStatusMeta[campaign.status]?.accent ?? "bg-slate-300"))}
      />
      <div className="flex flex-1 flex-col gap-3.5 p-4">
        <div className="flex items-start gap-3">
          <div
            aria-hidden="true"
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg",
              isHyper ? "bg-violet-50 text-violet-600" : "bg-brand-50 text-brand-600",
            )}
          >
            {isHyper ? <Sparkles className="h-5 w-5" /> : <Send className="h-5 w-5" />}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-[15px] font-semibold text-slate-900">
              {/* Stretched link: the whole card opens the campaign, while the
                  menu and footer action sit above it (relative z-10). */}
              <Link
                href={overviewHref}
                className="rounded after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
              >
                {campaign.name}
              </Link>
            </h3>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <CampaignStatusBadge status={campaign.status} />
              <CampaignTypeBadge type={campaign.campaign_type} />
            </div>
          </div>
          <div className="relative z-10 -mr-1 -mt-1">
            <DropdownMenu label={`Actions for ${campaign.name}`} items={menuItems} />
          </div>
        </div>

        <p
          className={cn(
            "line-clamp-2 min-h-[2.5rem] text-sm leading-5",
            campaign.description ? "text-slate-600" : "italic text-slate-400",
          )}
        >
          {campaign.description || "No description"}
        </p>

        <CampaignMetrics workspaceId={workspaceId} campaign={campaign} />

        <div className="mt-auto flex items-center justify-between gap-3 pt-1">
          <p
            className="min-w-0 truncate text-xs text-slate-500"
            title={new Date(campaign.updated_at).toLocaleString()}
          >
            Updated {formatRelativeTime(campaign.updated_at)}
          </p>
          <Button variant="outline" size="sm" asChild className="relative z-10 shrink-0">
            <Link href={overviewHref}>{mayDraft ? "Configure" : "View"}</Link>
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmArchive}
        title={`Archive "${campaign.name}"?`}
        description="It will no longer be editable."
        confirmLabel="Archive campaign"
        tone="danger"
        loading={archiveMutation.isPending}
        onConfirm={() => archiveMutation.mutate()}
        onCancel={() => setConfirmArchive(false)}
      />
    </article>
  );
}

export function CampaignCardSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="flex flex-col rounded-xl border border-slate-200 bg-white shadow-card"
    >
      <div className="h-1 rounded-t-xl bg-slate-200" />
      <div className="space-y-3.5 p-4">
        <div className="flex items-start gap-3">
          <Skeleton className="h-10 w-10 rounded-lg" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
        </div>
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-20 w-full rounded-lg" />
        <div className="flex items-center justify-between">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-24" />
        </div>
      </div>
    </div>
  );
}
