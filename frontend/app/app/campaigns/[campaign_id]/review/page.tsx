"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, Lock } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ProviderBadge } from "@/components/mailboxes/provider-badge";
import { ApiError } from "@/lib/api-client";
import { getReview } from "@/lib/campaigns-api";
import { canExecuteCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { PreflightIssue } from "@/types/domain";

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't load the review. Please try again.";
}

const TAB_FOR_FIELD: Record<string, string> = {
  sequence: "sequence",
  mailboxes: "senders",
  audience: "audience",
  settings: "schedule",
};

function tabForIssue(issue: PreflightIssue): string {
  const prefix = (issue.field_path ?? "").split(/[.[]/)[0];
  return TAB_FOR_FIELD[prefix] ?? "overview";
}

function IssueList({
  campaignId,
  issues,
  tone,
}: {
  campaignId: string;
  issues: PreflightIssue[];
  tone: "error" | "warning";
}) {
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-2">
      {issues.map((issue, i) => (
        <li
          key={`${issue.code}-${i}`}
          className={`flex items-start justify-between gap-3 rounded-md border p-3 text-sm ${
            tone === "error"
              ? "border-red-200 bg-red-50 text-red-800"
              : "border-amber-200 bg-amber-50 text-amber-800"
          }`}
        >
          <span>{issue.message}</span>
          <Link
            href={`/app/campaigns/${campaignId}/${tabForIssue(issue)}`}
            className="shrink-0 whitespace-nowrap text-xs font-medium underline"
          >
            Fix
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default function CampaignReviewPage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayExecute = canExecuteCampaign(activeWorkspace?.role_code);

  const reviewQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "campaigns", campaignId, "review"],
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getReview(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
    refetchOnWindowFocus: true,
  });

  if (reviewQuery.isLoading) {
    return (
      <div className="flex min-h-[200px] items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
      </div>
    );
  }
  if (reviewQuery.isError || !reviewQuery.data) {
    return <Alert variant="error">{errorMessage(reviewQuery.error)}</Alert>;
  }

  const { campaign, sequence, mailboxes, settings, audience, preflight } =
    reviewQuery.data;

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Review</h2>
        <p className="text-sm text-slate-500">
          Everything below reflects saved configuration. Preflight is
          re-checked here and again at activation.
        </p>
      </div>

      <div
        className={`rounded-lg border p-4 ${
          preflight.ready
            ? "border-emerald-200 bg-emerald-50"
            : "border-amber-200 bg-amber-50"
        }`}
      >
        <div
          className={`flex items-center gap-2 text-sm font-semibold ${
            preflight.ready ? "text-emerald-900" : "text-amber-900"
          }`}
        >
          {preflight.ready ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          {preflight.ready
            ? "Ready for activation (Phase 8)"
            : `${preflight.errors.length} item${
                preflight.errors.length === 1 ? "" : "s"
              } to resolve`}
        </div>
        <div className="mt-3 space-y-3">
          <IssueList campaignId={campaignId} issues={preflight.errors} tone="error" />
          <IssueList
            campaignId={campaignId}
            issues={preflight.warnings}
            tone="warning"
          />
        </div>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">Sequence</h3>
        <p className="mt-1 text-sm text-slate-600">
          {sequence && sequence.steps.length > 0
            ? `${sequence.steps.length} step${sequence.steps.length === 1 ? "" : "s"} (${
                sequence.steps.filter((s) => s.kind === "EMAIL").length
              } email, ${sequence.steps.filter((s) => s.kind === "WAIT").length} wait)`
            : "No steps configured."}
        </p>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">Senders</h3>
        {mailboxes.length === 0 ? (
          <p className="mt-1 text-sm text-slate-600">No mailboxes assigned.</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {mailboxes.map((mb) => (
              <li key={mb.mailbox_id} className="flex items-center gap-2 text-sm">
                <ProviderBadge provider={mb.provider} />
                <span className="text-slate-600">{mb.email_address}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">Schedule</h3>
        {settings ? (
          <p className="mt-1 text-sm text-slate-600">
            {settings.timezone}, {settings.window_start_local.slice(0, 5)}&ndash;
            {settings.window_end_local.slice(0, 5)}, limit{" "}
            {settings.daily_limit ?? "unlimited"}/day
          </p>
        ) : (
          <p className="mt-1 text-sm text-slate-600">No schedule configured.</p>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">Audience</h3>
        {audience && audience.is_committed ? (
          <p className="mt-1 text-sm text-slate-600">
            {audience.accepted_count ?? 0} eligible leads configured
            {(audience.excluded_count ?? 0) > 0 &&
              ` (${audience.excluded_count} excluded)`}
            . Final send eligibility is re-checked before anything sends.
          </p>
        ) : (
          <p className="mt-1 text-sm text-slate-600">No audience committed yet.</p>
        )}
      </section>

      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4">
        <Button disabled className="cursor-not-allowed opacity-60" title="Coming in a future phase">
          <Lock className="mr-1.5 h-4 w-4" />
          Start Campaign
        </Button>
        <p className="mt-2 text-xs text-slate-500">
          Campaign activation isn&apos;t available in this phase. Configuration
          stays safely in {campaign.status}
          {mayExecute ? "" : " -- only Managers and above can activate once available"}.
        </p>
      </div>
    </div>
  );
}
