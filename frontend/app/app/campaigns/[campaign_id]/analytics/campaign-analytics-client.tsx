"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  AlertTriangle,
  Ban,
  Clock,
  HelpCircle,
  Info,
  Loader2,
  MessageSquare,
  Send,
  Users,
} from "lucide-react";


import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  getCampaignAnalytics,
  getCampaignSequenceAnalytics,
} from "@/lib/analytics-api";
import { useWorkspace } from "@/lib/workspace-context";

function MetricCard({
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
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
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

export function CampaignAnalyticsClient() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const { activeWorkspaceId } = useWorkspace();

  const campaignQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId, "analytics"],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getCampaignAnalytics(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const sequenceQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId, "sequence-analytics"],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getCampaignSequenceAnalytics(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  if (campaignQuery.isLoading || sequenceQuery.isLoading) {
    return (
      <div className="flex min-h-[300px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
      </div>
    );
  }

  if (campaignQuery.isError || !campaignQuery.data) {
    return (
      <div className="space-y-4">
        <Alert variant="error">
          {campaignQuery.error instanceof Error
            ? campaignQuery.error.message
            : "Failed to load campaign analytics."}
        </Alert>
        <Button variant="outline" onClick={() => campaignQuery.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const camp = campaignQuery.data;
  const seqSteps = sequenceQuery.data?.steps || [];

  return (
    <div className="space-y-8">
      {/* Top KPI Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title="Total Recipients"
          value={camp.total_recipients.toLocaleString()}
          subtitle={`${camp.enrolled.toLocaleString()} enrolled`}
          icon={Users}
          color="indigo"
          tooltip="Deduplicated leads and recipient addresses enrolled in this campaign"
        />
        <MetricCard
          title="Messages Sent"
          value={camp.sent.toLocaleString()}
          subtitle={`${camp.unique_recipients_contacted.toLocaleString()} leads contacted`}
          icon={Send}
          color="emerald"
          tooltip="Authoritative sends accepted by provider. Retries do not inflate this count."
        />
        <MetricCard
          title="Replies"
          value={camp.replied.toLocaleString()}
          rate={camp.reply_rate}
          rateLabel="reply rate"
          icon={MessageSquare}
          color="indigo"
          subtitle={`${camp.unique_recipients_replied.toLocaleString()} unique replied`}
          tooltip="Inbound replies correlated to this campaign. Deduplicated by recipient enrollment."
        />
        <MetricCard
          title="Hard Bounces"
          value={camp.bounced.toLocaleString()}
          rate={camp.bounce_rate}
          rateLabel="bounce rate"
          icon={AlertOctagon}
          color="rose"
          tooltip="Recipients whose messages permanently bounced and were suppressed"
        />
      </div>

      {/* Secondary Metrics */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title="Unsubscribes"
          value={camp.unsubscribed.toLocaleString()}
          rate={camp.unsubscribe_rate}
          rateLabel="unsub rate"
          icon={Ban}
          color="slate"
          tooltip="Recipients who unsubscribed from this campaign"
        />
        <MetricCard
          title="Complaints"
          value={camp.complained.toLocaleString()}
          rate={camp.complaint_rate}
          rateLabel="complaint rate"
          icon={AlertTriangle}
          color="amber"
          tooltip="Normalized provider spam reports"
        />
        <MetricCard
          title="Remaining / Scheduled"
          value={camp.remaining.toLocaleString()}
          subtitle={`${camp.scheduled.toLocaleString()} due/queued`}
          icon={Clock}
          color="slate"
          tooltip="Messages planned or queued for future sequence steps"
        />
        <MetricCard
          title="Send Failures"
          value={camp.failed.toLocaleString()}
          rate={camp.failure_rate}
          rateLabel="failure rate"
          icon={AlertTriangle}
          color="rose"
          tooltip="Messages that exhausted retries or failed terminal sending conditions"
        />
      </div>

      {/* Recipient vs Message Metrics Info Card */}
      <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-5">
        <div className="flex items-start gap-3">
          <Info className="mt-0.5 h-5 w-5 text-indigo-600" />
          <div className="space-y-1 text-xs text-slate-600">
            <p className="font-semibold text-slate-900">
              Message Metrics vs. Recipient Metrics
            </p>
            <p>
              In multi-step sequences, recipients may receive multiple emails. This dashboard
              maintains strict distinction: <strong className="text-slate-900">Contacted Leads</strong> (
              {camp.unique_recipients_contacted.toLocaleString()}) and{" "}
              <strong className="text-slate-900">Replied Leads</strong> (
              {camp.unique_recipients_replied.toLocaleString()}) are deduplicated by recipient identity,
              while <strong className="text-slate-900">Sent</strong> and{" "}
              <strong className="text-slate-900">Failures</strong> measure individual message dispatches.
            </p>
          </div>
        </div>
      </div>

      {/* Sequence Step Breakdown */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-center justify-between border-b border-slate-100 pb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">
              Sequence Step Performance
            </h2>
            <p className="text-xs text-slate-500">
              Authoritative step attribution: replies are attributed specifically to the message step
              that provoked them.
            </p>
          </div>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600">
            {seqSteps.length} Step{seqSteps.length === 1 ? "" : "s"}
          </span>
        </div>

        {seqSteps.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-500">
            No sequence steps found for this campaign.
          </div>
        ) : (
          <div className="space-y-4">
            {seqSteps.map((step) => {
              return (
                <div
                  key={step.step_id}
                  className="rounded-lg border border-slate-200 bg-slate-50/50 p-4 transition hover:bg-slate-50"
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-3">
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-100 text-xs font-bold text-indigo-700">
                        {step.position}
                      </span>
                      <div>
                        <p className="text-xs font-semibold text-slate-900">
                          {step.subject || `Step ${step.position} (No Subject)`}
                        </p>
                        <p className="text-[11px] text-slate-500">Kind: {step.kind}</p>
                      </div>
                    </div>

                    {/* Step Metrics Counters */}
                    <div className="flex flex-wrap items-center gap-6 text-xs">
                      <div>
                        <p className="text-slate-400">Sent</p>
                        <p className="font-semibold text-slate-900">
                          {step.sent.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="text-slate-400">Replies</p>
                        <p className="font-semibold text-emerald-600">
                          {step.replied.toLocaleString()}{" "}
                          <span className="text-[11px] font-normal text-slate-500">
                            ({step.reply_rate}%)
                          </span>
                        </p>
                      </div>
                      <div>
                        <p className="text-slate-400">Bounces</p>
                        <p className="font-semibold text-rose-600">
                          {step.bounced.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="text-slate-400">Unsubs</p>
                        <p className="font-semibold text-slate-700">
                          {step.unsubscribed.toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <p className="text-slate-400">Scheduled</p>
                        <p className="font-semibold text-slate-700">
                          {step.scheduled.toLocaleString()}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
