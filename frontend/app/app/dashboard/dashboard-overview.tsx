"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  AlertTriangle,
  ArrowRight,
  Ban,
  BarChart3,
  Calendar,
  Eye,
  Inbox,
  Info,
  Mail,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  UploadCloud,
  Users,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { MetricCard } from "@/components/ui/metric-card";
import { Skeleton } from "@/components/ui/skeleton";
import { CampaignStatusBadge } from "@/components/ui/status-badge";
import { getDeliverabilityOverview, getWorkspaceOverview } from "@/lib/analytics-api";
import { errorMessage } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { WorkspaceOverviewAnalytics } from "@/types/analytics";
import type { CampaignStatus } from "@/types/domain";

import { DATE_PRESETS, getDateRangeForPreset, type DatePreset } from "./date-presets";
import { DeliverabilityDetails, DeliverabilityHealth } from "./deliverability-sections";
import { TrendChart } from "./trend-chart";

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

// Native tooltip keeps the metric definitions the old Analytics page carried
// without adding a tooltip component to MetricCard.
function Labeled({ text, tooltip }: { text: string; tooltip: string }) {
  return (
    <span title={tooltip} className="cursor-help">
      {text}
    </span>
  );
}

// Workspace numbers come from two authoritative endpoints (analytics overview
// and deliverability) that share one date window. Nothing here is computed
// client-side beyond formatting, and the two queries fail independently.
export function DashboardOverview() {
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const [preset, setPreset] = useState<DatePreset>("last_30_days");

  const { startDate, endDate } = getDateRangeForPreset(preset);

  const overviewQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "analytics", "overview", startDate, endDate],
    queryFn: () =>
      activeWorkspaceId
        ? getWorkspaceOverview(activeWorkspaceId, { startDate, endDate })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const deliverabilityQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "analytics", "deliverability", startDate, endDate],
    queryFn: () =>
      activeWorkspaceId
        ? getDeliverabilityOverview(activeWorkspaceId, { startDate, endDate })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const refreshing = overviewQuery.isFetching || deliverabilityQuery.isFetching;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div
          role="group"
          aria-label="Date range"
          className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-card"
        >
          {DATE_PRESETS.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              aria-pressed={preset === value}
              onClick={() => setPreset(value)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
                preset === value
                  ? "bg-brand-600 text-white shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={refreshing}
          onClick={() => {
            void overviewQuery.refetch();
            void deliverabilityQuery.refetch();
          }}
        >
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Refresh
        </Button>
      </div>

      <section aria-labelledby="performance-heading" className="space-y-6">
        <h2 id="performance-heading" className="text-base font-semibold text-slate-900">
          Outreach performance
        </h2>

        {overviewQuery.isLoading ? (
          <OverviewSkeleton />
        ) : overviewQuery.isError || !overviewQuery.data ? (
          <div className="space-y-3">
            <Alert variant="error">
              {errorMessage(overviewQuery.error, "We couldn't load your workspace overview.")}
            </Alert>
            <Button variant="outline" size="sm" onClick={() => overviewQuery.refetch()}>
              Try again
            </Button>
          </div>
        ) : (
          <PerformanceSections data={overviewQuery.data} mayDraft={mayDraft} />
        )}
      </section>

      <section aria-labelledby="deliverability-heading" className="space-y-6">
        <h2 id="deliverability-heading" className="text-base font-semibold text-slate-900">
          Deliverability
        </h2>

        {deliverabilityQuery.isLoading ? (
          <OverviewSkeleton />
        ) : deliverabilityQuery.isError || !deliverabilityQuery.data ? (
          <div className="space-y-3">
            <Alert variant="error">
              {errorMessage(deliverabilityQuery.error, "We couldn't load deliverability metrics.")}
            </Alert>
            <Button variant="outline" size="sm" onClick={() => deliverabilityQuery.refetch()}>
              Try again
            </Button>
          </div>
        ) : (
          <>
            <DeliverabilityHealth data={deliverabilityQuery.data} />
            <DeliverabilityDetails data={deliverabilityQuery.data} />
          </>
        )}
      </section>
    </div>
  );
}

function PerformanceSections({
  data,
  mayDraft,
}: {
  data: WorkspaceOverviewAnalytics;
  mayDraft: boolean;
}) {
  const opensTracked = data.open_tracking_supported;
  const hasActivity = data.emails_sent > 0 || data.top_campaigns.some((c) => c.sent > 0);

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label={
            <Labeled
              text="Prospects contacted"
              tooltip="Deduplicated recipients who were sent at least one email during this period"
            />
          }
          value={formatNumber(data.prospects_contacted)}
          hint="Unique recipient addresses"
          icon={<Users />}
        />
        <MetricCard
          label={
            <Labeled
              text="Emails sent"
              tooltip="Authoritative sends accepted by Gmail, Microsoft, or SMTP providers. Retries do not inflate this count."
            />
          }
          value={formatNumber(data.emails_sent)}
          hint="Provider accepted"
          icon={<Send />}
        />
        <MetricCard
          label={
            <Labeled
              text="Replies"
              tooltip="Authoritative inbound replies correlated by reply synchronization"
            />
          }
          value={formatNumber(data.replies)}
          hint={`${data.reply_rate.toFixed(1)}% reply rate`}
          icon={<MessageSquare />}
        />
        <MetricCard
          label={
            <Labeled
              text="Bounced"
              tooltip="Emails sent in this period that were reported undeliverable, each counted once. Bounce rate = bounced / sent."
            />
          }
          value={formatNumber(data.bounces)}
          hint={`${data.bounce_rate.toFixed(1)}% bounce rate`}
          icon={<AlertOctagon />}
        />
        <MetricCard
          label={
            <Labeled
              text="Unsubscribes"
              tooltip="Recipients suppressed via verified unsubscribe tokens or headers"
            />
          }
          value={formatNumber(data.unsubscribes)}
          hint={`${data.unsubscribe_rate.toFixed(1)}% unsubscribe rate`}
          icon={<Ban />}
        />
        <MetricCard
          label={
            <Labeled
              text="Complaints"
              tooltip="Normalized provider spam reports (Google/Yahoo threshold: 0.1%)"
            />
          }
          value={formatNumber(data.complaints)}
          hint={`${data.complaint_rate.toFixed(1)}% complaint rate`}
          icon={<AlertTriangle />}
        />
        {opensTracked ? (
          <MetricCard
            label={
              <Labeled
                text="Opened"
                tooltip="Delivered emails opened at least once. Open rate = opened / delivered (sent minus bounced). Opens use a tiny image; privacy features can inflate or hide them."
              />
            }
            value={formatNumber(data.opens ?? 0)}
            hint={`${(data.open_rate ?? 0).toFixed(1)}% open rate (estimated)`}
            icon={<Eye />}
          />
        ) : (
          <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-4 shadow-sm">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[13px] font-medium text-slate-500">Opens &amp; Clicks</p>
              <BarChart3 className="h-4 w-4 text-slate-400" aria-hidden="true" />
            </div>
            <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
              <Info className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
              Tracking Not Enabled
            </div>
            <p className="mt-2 text-xs leading-relaxed text-slate-500">
              Open pixel and link click tracking are not active to maximize inbox deliverability.
            </p>
          </div>
        )}
        <MetricCard
          label={
            <Labeled
              text="Send failures"
              tooltip="Messages that exhausted retries or encountered terminal provider failures"
            />
          }
          value={formatNumber(data.failed_sends)}
          hint="Permanent sending errors"
          icon={<AlertTriangle />}
        />
      </div>

      <Card>
        <CardHeader
          title="Activity over time"
          description={`Daily email volume from ${data.start_date} to ${data.end_date} (${data.timezone})`}
          action={
            <span className="flex items-center gap-1 text-xs text-slate-400">
              <Calendar className="h-3.5 w-3.5" aria-hidden="true" />
              UTC calendar buckets
            </span>
          }
        />
        <div className="mt-4">
          <TrendChart trend={data.trend} />
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Top campaigns"
            description="By emails sent in this date range"
            action={
              <Link
                href="/app/campaigns"
                className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-900"
              >
                All campaigns
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            }
          />
          {data.top_campaigns.length === 0 ? (
            <div className="mt-4 text-sm text-slate-500">
              <p>No campaign sends recorded in this date range.</p>
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
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-100 text-slate-400">
                    <th className="pb-2 font-medium">Campaign</th>
                    <th className="pb-2 text-right font-medium">Sent</th>
                    <th className="pb-2 text-right font-medium">Replies</th>
                    <th className="pb-2 text-right font-medium">Bounces</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.top_campaigns.map((campaign) => (
                    <tr key={campaign.campaign_id} className="hover:bg-slate-50/50">
                      <td className="py-2.5">
                        <div className="flex items-center gap-2">
                          <Link
                            href={`/app/campaigns/${campaign.campaign_id}/overview`}
                            className="min-w-0 truncate text-sm font-medium text-slate-900 hover:text-brand-700"
                          >
                            {campaign.name}
                          </Link>
                          <CampaignStatusBadge status={campaign.status as CampaignStatus} />
                        </div>
                      </td>
                      <td className="py-2.5 text-right text-slate-700">
                        {formatNumber(campaign.sent)}
                      </td>
                      <td className="py-2.5 text-right font-medium text-emerald-600">
                        {formatNumber(campaign.replies)} ({campaign.reply_rate.toFixed(1)}%)
                      </td>
                      <td className="py-2.5 text-right font-medium text-red-600">
                        {formatNumber(campaign.bounces)} ({campaign.bounce_rate.toFixed(1)}%)
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Top mailboxes"
            description="By emails sent in this date range"
            action={
              <Link
                href="/app/mailboxes"
                className="inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-900"
              >
                All mailboxes
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            }
          />
          {data.top_mailboxes.length === 0 ? (
            <div className="mt-4 text-sm text-slate-500">
              No mailbox activity recorded in this date range.
            </div>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-100 text-slate-400">
                    <th className="pb-2 font-medium">Address</th>
                    <th className="pb-2 text-right font-medium">Sent</th>
                    <th className="pb-2 text-right font-medium">Replies</th>
                    <th className="pb-2 text-right font-medium">Bounces</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.top_mailboxes.map((mb) => (
                    <tr key={mb.mailbox_id} className="hover:bg-slate-50/50">
                      <td className="py-2.5 font-medium text-slate-900">
                        <div className="flex items-center gap-1.5">
                          <Mail className="h-3 w-3 text-slate-400" aria-hidden="true" />
                          <span>{mb.email_address}</span>
                        </div>
                      </td>
                      <td className="py-2.5 text-right text-slate-700">
                        {formatNumber(mb.sent)}
                      </td>
                      <td className="py-2.5 text-right font-medium text-emerald-600">
                        {formatNumber(mb.replies)} ({mb.reply_rate.toFixed(1)}%)
                      </td>
                      <td className="py-2.5 text-right font-medium text-red-600">
                        {formatNumber(mb.bounces)} ({mb.bounce_rate.toFixed(1)}%)
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
    </>
  );
}
