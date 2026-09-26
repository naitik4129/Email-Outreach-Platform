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
  HelpCircle,
  Info,
  Loader2,
  Mail,
  MessageSquare,
  Send,
  Users,
} from "lucide-react";


import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getWorkspaceOverview } from "@/lib/analytics-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { TimeSeriesBucket, WorkspaceOverviewAnalytics } from "@/types/analytics";

type DatePreset = "today" | "yesterday" | "last_7_days" | "last_30_days";

function getDateRangeForPreset(preset: DatePreset): { startDate: string; endDate: string } {
  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);

  if (preset === "today") {
    return { startDate: todayStr, endDate: todayStr };
  }
  if (preset === "yesterday") {
    const yest = new Date(now);
    yest.setDate(now.getDate() - 1);
    const yestStr = yest.toISOString().slice(0, 10);
    return { startDate: yestStr, endDate: yestStr };
  }
  if (preset === "last_7_days") {
    const d7 = new Date(now);
    d7.setDate(now.getDate() - 6);
    return { startDate: d7.toISOString().slice(0, 10), endDate: todayStr };
  }
  // last_30_days
  const d30 = new Date(now);
  d30.setDate(now.getDate() - 29);
  return { startDate: d30.toISOString().slice(0, 10), endDate: todayStr };
}

function StatCard({
  title,
  value,
  rate,
  rateLabel,
  icon: Icon,
  color = "indigo",
  subtitle,
  tooltip,
}: {
  title: string;
  value: string | number;
  rate?: number | null;
  rateLabel?: string;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;
  color?: "indigo" | "emerald" | "amber" | "rose" | "slate";
  subtitle?: string;
  tooltip?: string;
}) {
  const bgStyles = {
    indigo: "bg-indigo-50 text-indigo-700",
    emerald: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    rose: "bg-rose-50 text-rose-700",
    slate: "bg-slate-100 text-slate-700",
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-1.5">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              {title}
            </p>
            {tooltip && (
              <span title={tooltip} className="cursor-help text-slate-400 hover:text-slate-600">
                <HelpCircle className="h-3.5 w-3.5" />
              </span>
            )}
          </div>
          <p className="mt-2 text-2xl font-bold tracking-tight text-slate-900">{value}</p>
        </div>
        <div className={`grid h-10 w-10 place-items-center rounded-lg ${bgStyles[color]}`}>
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>
      {(rate !== undefined && rate !== null) || subtitle ? (
        <div className="mt-3 flex items-center gap-2 border-t border-slate-100 pt-3 text-xs text-slate-600">
          {rate !== undefined && rate !== null && (
            <span className="font-semibold text-slate-900">
              {rate}% <span className="font-normal text-slate-500">{rateLabel || "rate"}</span>
            </span>
          )}
          {subtitle && <span className="text-slate-500">{subtitle}</span>}
        </div>
      ) : null}
    </div>
  );
}

function TrendChart({ trend }: { trend: TimeSeriesBucket[] }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (!trend || trend.length === 0) {
    return (
      <div className="grid h-48 place-items-center rounded-lg border border-dashed border-slate-200 text-sm text-slate-500">
        No trend data available for this date window.
      </div>
    );
  }

  const maxSent = Math.max(...trend.map((t) => t.sent), 1);
  const chartHeight = 160;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-6 text-xs">
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-indigo-600" />
            <span className="font-medium text-slate-700">Emails Sent</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-emerald-500" />
            <span className="font-medium text-slate-700">Replies</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-3 w-3 rounded-sm bg-rose-500" />
            <span className="font-medium text-slate-700">Bounces</span>
          </div>
        </div>
        {hoveredIndex !== null && trend[hoveredIndex] && (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700 shadow-sm">
            <span className="font-semibold text-slate-900">{trend[hoveredIndex].date}:</span>{" "}
            {trend[hoveredIndex].sent} sent &bull; {trend[hoveredIndex].replies} replies &bull;{" "}
            {trend[hoveredIndex].bounces} bounces
          </div>
        )}
      </div>

      <div className="relative pt-6">
        <div className="flex h-44 items-end gap-1.5 sm:gap-2">
          {trend.map((bucket, idx) => {
            const sentHeight = Math.max(Math.round((bucket.sent / maxSent) * chartHeight), 4);
            const isHovered = hoveredIndex === idx;

            return (
              <div
                key={bucket.date}
                className="group relative flex flex-1 flex-col items-center"
                onMouseEnter={() => setHoveredIndex(idx)}
                onMouseLeave={() => setHoveredIndex(null)}
              >
                {/* Visual tooltip */}
                <div
                  className={`pointer-events-none absolute -top-12 z-10 hidden whitespace-nowrap rounded bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white shadow transition-all group-hover:block`}
                >
                  <p className="font-semibold">{bucket.date}</p>
                  <p>
                    Sent: {bucket.sent} | Rep: {bucket.replies} | Bnc: {bucket.bounces}
                  </p>
                </div>

                {/* Stacked bar visualization */}
                <div className="flex w-full max-w-[28px] flex-col items-center justify-end">
                  <div
                    style={{ height: `${sentHeight}px` }}
                    className={`w-full rounded-t transition-all ${
                      isHovered ? "bg-indigo-700 ring-2 ring-indigo-400" : "bg-indigo-500/90"
                    }`}
                  />
                </div>

                {/* X axis date label */}
                <span className="mt-2 block truncate text-[10px] text-slate-400">
                  {trend.length <= 10
                    ? bucket.date.slice(5)
                    : idx % Math.ceil(trend.length / 7) === 0
                      ? bucket.date.slice(5)
                      : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function AnalyticsPageClient() {
  const { activeWorkspaceId } = useWorkspace();
  const [preset, setPreset] = useState<DatePreset>("last_30_days");

  const dateBounds = getDateRangeForPreset(preset);

  const query = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "analytics",
      "overview",
      dateBounds.startDate,
      dateBounds.endDate,
    ],
    queryFn: () =>
      activeWorkspaceId
        ? getWorkspaceOverview(activeWorkspaceId, {
            startDate: dateBounds.startDate,
            endDate: dateBounds.endDate,
          })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  if (query.isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="space-y-4">
        <Alert variant="error">
          {query.error instanceof Error
            ? query.error.message
            : "Failed to load workspace analytics."}
        </Alert>
        <Button variant="outline" onClick={() => query.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const data: WorkspaceOverviewAnalytics = query.data;

  return (

    <div className="space-y-8">
      {/* Header and Filter Controls */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Outreach Analytics</h1>
          <p className="mt-1 text-sm text-slate-500">
            Trustworthy metrics derived directly from authoritative message and response events.
          </p>
        </div>

        {/* Date presets */}
        <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          <button
            type="button"
            onClick={() => setPreset("today")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              preset === "today"
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            Today
          </button>
          <button
            type="button"
            onClick={() => setPreset("yesterday")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              preset === "yesterday"
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            Yesterday
          </button>
          <button
            type="button"
            onClick={() => setPreset("last_7_days")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              preset === "last_7_days"
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            7 Days
          </button>
          <button
            type="button"
            onClick={() => setPreset("last_30_days")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              preset === "last_30_days"
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            30 Days
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Prospects Contacted"
          value={data.prospects_contacted.toLocaleString()}
          icon={Users}
          color="indigo"
          subtitle="Unique recipient addresses"
          tooltip="Deduplicated recipients who were sent at least one email during this period"
        />
        <StatCard
          title="Emails Sent"
          value={data.emails_sent.toLocaleString()}
          icon={Send}
          color="emerald"
          subtitle="Provider accepted"
          tooltip="Authoritative sends accepted by Gmail, Microsoft, or SMTP providers. Retries do not inflate this count."
        />
        <StatCard
          title="Replies"
          value={data.replies.toLocaleString()}
          rate={data.reply_rate}
          rateLabel="reply rate"
          icon={MessageSquare}
          color="indigo"
          tooltip="Authoritative inbound replies correlated by Phase 13 reply synchronization"
        />
        <StatCard
          title="Hard Bounces"
          value={data.bounces.toLocaleString()}
          rate={data.bounce_rate}
          rateLabel="bounce rate"
          icon={AlertOctagon}
          color="rose"
          tooltip="Emails sent in this period that were reported undeliverable, each counted once. Bounce rate = bounced / sent."
        />
      </div>

      {/* Secondary Metrics Row */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Unsubscribes"
          value={data.unsubscribes.toLocaleString()}
          rate={data.unsubscribe_rate}
          rateLabel="unsub rate"
          icon={Ban}
          color="slate"
          tooltip="Recipients suppressed via verified unsubscribe tokens or headers"
        />
        <StatCard
          title="Complaints"
          value={data.complaints.toLocaleString()}
          rate={data.complaint_rate}
          rateLabel="complaint rate"
          icon={AlertTriangle}
          color="amber"
          tooltip="Normalized provider spam reports (Google/Yahoo threshold: 0.1%)"
        />
        {data.open_tracking_supported ? (
          <StatCard
            title="Opened"
            value={(data.opens ?? 0).toLocaleString()}
            rate={data.open_rate ?? 0}
            rateLabel="open rate"
            icon={BarChart3}
            color="indigo"
            subtitle="Estimated"
            tooltip="Delivered emails opened at least once. Open rate = opened / delivered (sent minus bounced). Opens use a tiny image; privacy features can inflate or hide them."
          />
        ) : (
        /* Honest open and click tracking card */
        <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                Opens & Clicks
              </p>
              <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
                <Info className="h-3.5 w-3.5 text-slate-400" />
                Tracking Not Enabled
              </div>
            </div>
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-slate-100 text-slate-400">
              <BarChart3 className="h-5 w-5" aria-hidden="true" />
            </div>
          </div>
          <p className="mt-3 text-xs leading-relaxed text-slate-500">
            Open pixel and link click tracking are not active to maximize inbox deliverability.
          </p>
        </div>
        )}

        <StatCard
          title="Send Failures"
          value={data.failed_sends.toLocaleString()}
          icon={AlertTriangle}
          color="rose"
          subtitle="Permanent sending errors"
          tooltip="Messages that exhausted retries or encountered terminal provider failures"
        />
      </div>

      {/* Trend Chart Card */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-4 flex items-center justify-between border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Activity Over Time</h2>
            <p className="text-xs text-slate-500">
              Daily email volume, replies, and bounces from {data.start_date} to {data.end_date} (
              {data.timezone})
            </p>
          </div>
          <div className="flex items-center gap-1 text-xs text-slate-400">
            <Calendar className="h-3.5 w-3.5" />
            <span>UTC Calendar Buckets</span>
          </div>
        </div>
        <TrendChart trend={data.trend} />
      </div>

      {/* Performance Tables Grid */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Top Campaigns Table */}
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between border-b border-slate-100 pb-3">
            <h2 className="text-base font-semibold text-slate-900">Top Campaigns</h2>
            <Link
              href="/app/campaigns"
              className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              All campaigns <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {data.top_campaigns.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-500">
              No campaign sends recorded in this date range.
            </div>
          ) : (
            <div className="overflow-x-auto">
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
                  {data.top_campaigns.map((camp) => (
                    <tr key={camp.campaign_id} className="hover:bg-slate-50/50">
                      <td className="py-2.5 font-medium text-slate-900">
                        <Link
                          href={`/app/campaigns/${camp.campaign_id}/analytics`}
                          className="hover:text-indigo-600 hover:underline"
                        >
                          {camp.name}
                        </Link>
                      </td>
                      <td className="py-2.5 text-right text-slate-700">
                        {camp.sent.toLocaleString()}
                      </td>
                      <td className="py-2.5 text-right font-medium text-emerald-600">
                        {camp.replies} ({camp.reply_rate}%)
                      </td>
                      <td className="py-2.5 text-right font-medium text-rose-600">
                        {camp.bounces} ({camp.bounce_rate}%)
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Top Mailboxes Table */}
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between border-b border-slate-100 pb-3">
            <h2 className="text-base font-semibold text-slate-900">Top Mailboxes</h2>
            <Link
              href="/app/mailboxes"
              className="inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-800"
            >
              All mailboxes <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {data.top_mailboxes.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-500">
              No mailbox activity recorded in this date range.
            </div>
          ) : (
            <div className="overflow-x-auto">
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
                          <Mail className="h-3 w-3 text-slate-400" />
                          <span>{mb.email_address}</span>
                        </div>
                      </td>
                      <td className="py-2.5 text-right text-slate-700">
                        {mb.sent.toLocaleString()}
                      </td>
                      <td className="py-2.5 text-right font-medium text-emerald-600">
                        {mb.replies} ({mb.reply_rate}%)
                      </td>
                      <td className="py-2.5 text-right font-medium text-rose-600">
                        {mb.bounces} ({mb.bounce_rate}%)
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
