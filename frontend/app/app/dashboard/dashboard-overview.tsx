"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Eye,
  Inbox,
  Mail,
  MessageSquare,
  Plus,
  Send,
  UploadCloud,
} from "lucide-react";

import { TrendChart } from "@/app/app/analytics/analytics-page-client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { MetricCard } from "@/components/ui/metric-card";
import { Skeleton } from "@/components/ui/skeleton";
import { CampaignStatusBadge } from "@/components/ui/status-badge";
import { getWorkspaceOverview } from "@/lib/analytics-api";
import { errorMessage } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignStatus } from "@/types/domain";

const QUICK_ACTIONS = [
  { href: "/app/mailboxes/connect", label: "Connect a mailbox", hint: "Choose who your emails send from", icon: Mail },
  { href: "/app/leads/imports/new", label: "Import leads", hint: "Upload a CSV and map columns", icon: UploadCloud },
  { href: "/app/inbox", label: "Open inbox", hint: "Read and reply to conversations", icon: Inbox },
];

function OverviewSkeleton() {
  return (
    <div aria-busy="true" className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-3 w-28" />
          </div>
        ))}
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <Skeleton className="mb-4 h-5 w-40" />
        <Skeleton className="h-44 w-full" />
      </div>
    </div>
  );
}

// Real workspace numbers from the existing analytics overview (default window:
// the last 30 days). Nothing here is computed client-side beyond formatting.
export function DashboardOverview() {
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);

  const query = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "analytics", "overview", "dashboard"],
    queryFn: () =>
      activeWorkspaceId
        ? getWorkspaceOverview(activeWorkspaceId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  if (query.isLoading) return <OverviewSkeleton />;

  if (query.isError || !query.data) {
    return (
      <div className="space-y-3">
        <Alert variant="error">
          {errorMessage(query.error, "We couldn't load your workspace overview.")}
        </Alert>
        <Button variant="outline" size="sm" onClick={() => query.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  const data = query.data;
  const opensTracked = data.open_tracking_supported;
  const hasActivity = data.emails_sent > 0 || data.top_campaigns.some((c) => c.sent > 0);
  const topCampaigns = data.top_campaigns;

  return (
    <div className="space-y-6">
      <div
        className={
          opensTracked
            ? "grid gap-4 sm:grid-cols-2 xl:grid-cols-4"
            : "grid gap-4 sm:grid-cols-3"
        }
      >
        <MetricCard
          label="Emails sent"
          value={formatNumber(data.emails_sent)}
          hint={`${formatNumber(data.prospects_contacted)} prospects contacted`}
          icon={<Send />}
        />
        {opensTracked ? (
          <MetricCard
            label="Opened"
            value={formatNumber(data.opens ?? 0)}
            hint={`${(data.open_rate ?? 0).toFixed(1)}% open rate`}
            icon={<Eye />}
          />
        ) : null}
        <MetricCard
          label="Replies"
          value={formatNumber(data.replies)}
          hint={`${data.reply_rate.toFixed(1)}% reply rate`}
          icon={<MessageSquare />}
        />
        <MetricCard
          label="Bounced"
          value={formatNumber(data.bounces)}
          hint={`${data.bounce_rate.toFixed(1)}% bounce rate`}
          icon={<AlertTriangle />}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Sending activity" description="Last 30 days" />
          <div className="mt-4">
            <TrendChart trend={data.trend} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Top campaigns" description="By emails sent, last 30 days" />
          {topCampaigns.length === 0 ? (
            <div className="mt-4 text-sm text-slate-500">
              <p>No campaigns yet.</p>
              {mayDraft ? (
                <Button asChild size="sm" className="mt-3">
                  <Link href="/app/campaigns/new">
                    <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                    Create Campaign
                  </Link>
                </Button>
              ) : null}
            </div>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {topCampaigns.map((campaign) => (
                <li key={campaign.campaign_id} className="py-3">
                  <div className="flex items-center justify-between gap-2">
                    <Link
                      href={`/app/campaigns/${campaign.campaign_id}/overview`}
                      className="min-w-0 truncate text-sm font-medium text-slate-900 hover:text-brand-700"
                    >
                      {campaign.name}
                    </Link>
                    <CampaignStatusBadge status={campaign.status as CampaignStatus} />
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {formatNumber(campaign.sent)} sent · {formatNumber(campaign.replies)} replies (
                    {campaign.reply_rate.toFixed(1)}%)
                  </p>
                </li>
              ))}
            </ul>
          )}
          <Link
            href="/app/campaigns"
            className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:text-brand-900"
          >
            All campaigns
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>
        </Card>
      </div>

      {!hasActivity ? (
        <Card className="border-dashed">
          <CardHeader
            title="Get set up to send"
            description="Nothing has been sent in this window yet. These steps get a campaign ready to launch."
          />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {QUICK_ACTIONS.map(({ href, label, hint, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className="group flex items-start gap-3 rounded-lg border border-slate-200 p-3 transition-colors hover:border-brand-300 hover:bg-brand-50/40"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-slate-900">{label}</span>
                  <span className="block text-xs text-slate-500">{hint}</span>
                </span>
              </Link>
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
