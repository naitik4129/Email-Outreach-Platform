"use client";

import * as React from "react";
import { AlertTriangle, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { EmailFrame } from "@/components/campaigns/personalization/email-frame";
import { describeFailureCode } from "@/components/campaigns/personalization/failure-codes";
import { SampleLeadPicker } from "@/components/campaigns/personalization/sample-lead-picker";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import type { PreviewBatch, PreviewItem } from "@/types/domain";
import { LoadingBlock } from "@/components/ui/skeleton";

// A sample batch normally finishes within a couple of minutes; the server itself
// gives up on a failing one after about four.
const STALLED_AFTER_MS = 6 * 60 * 1000;

type Props = {
  batch: PreviewBatch | null;
  loading: boolean;
  loadError: unknown;
  // Editing samples requires campaigns.draft and a DRAFT campaign.
  canGenerate: boolean;
  // Why generating is not possible right now (e.g. no objective yet), or null.
  blockedReason: string | null;
  generating: boolean;
  generateError: unknown;
  workspaceId: string | null | undefined;
  campaignId: string;
  // Generates the sample for the one lead the user picked.
  onGenerate: (audienceMemberId: string) => Promise<unknown> | void;
};

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function recipientName(item: PreviewItem) {
  const { first_name, last_name } = item.recipient;
  return [first_name, last_name].filter(Boolean).join(" ") || "Lead";
}

function groupByRecipient(items: PreviewItem[]) {
  const groups = new Map<string, PreviewItem[]>();
  for (const item of items) {
    const key = item.recipient.audience_member_id;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.values()].map((group) =>
    [...group].sort((a, b) => a.step_position - b.step_position),
  );
}

function SampleCard({ item, emailNumber }: { item: PreviewItem; emailNumber: number }) {
  return (
    <div className="space-y-2 rounded-md border border-slate-200 p-3" data-testid="sample">
      <div className="flex items-center justify-between text-xs font-medium text-slate-500">
        <span>Email {emailNumber}</span>
        {item.state === "PENDING" ? (
          <span className="inline-flex items-center gap-1 text-sky-700">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
            Generating…
          </span>
        ) : null}
        {item.fallback_used ? (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800">
            Standard email — not enough data to personalize
          </span>
        ) : null}
      </div>

      {item.state === "FAILED" ? (
        <Alert variant="error">
          <span>
            This sample could not be generated.
            <ul className="mt-1 list-disc pl-4 font-normal">
              {item.failure_codes.map((code) => (
                <li key={code}>{describeFailureCode(code)}</li>
              ))}
            </ul>
          </span>
        </Alert>
      ) : null}

      {item.state === "OK" && item.subject && item.body_html ? (
        <>
          <p className="text-sm">
            <span className="font-semibold text-slate-700">Subject: </span>
            <span className="text-slate-900">{item.subject}</span>
          </p>
          <EmailFrame html={item.body_html} title={`Email ${emailNumber} for ${item.recipient.first_name ?? "lead"}`} />
        </>
      ) : null}
    </div>
  );
}

export function SamplePreviewPanel({
  batch,
  loading,
  loadError,
  canGenerate,
  blockedReason,
  generating,
  generateError,
  workspaceId,
  campaignId,
  onGenerate,
}: Props) {
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const groups = batch ? groupByRecipient(batch.items) : [];
  const failed = batch ? batch.items.filter((i) => i.state === "FAILED").length : 0;
  const blocked = !canGenerate || Boolean(blockedReason);
  // The page polls while samples are written by a background worker. If that worker
  // is down or crashed nothing ever finishes them, so after a while stop claiming
  // progress and let the person start over.
  const stalled =
    batch !== null &&
    batch !== undefined &&
    !batch.complete &&
    Date.now() - new Date(batch.created_at).getTime() > STALLED_AFTER_MS;

  return (
    <section
      aria-label="Sample emails"
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-base font-semibold text-slate-900">
            <Sparkles className="h-4 w-4 text-violet-600" aria-hidden="true" />
            Sample emails
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            See what your recipients will receive. Choose a lead and we write a personalized
            version of every email in the sequence for them.
          </p>
        </div>
        {canGenerate ? (
          <Button
            variant={batch ? "outline" : "primary"}
            disabled={blocked || generating || (batch ? !batch.complete && !stalled : false)}
            onClick={() => setPickerOpen(true)}
            title={blockedReason ?? undefined}
          >
            {generating ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
            )}
            {batch ? "Generate new samples" : "Generate samples"}
          </Button>
        ) : null}
      </div>

      <SampleLeadPicker
        open={pickerOpen}
        workspaceId={workspaceId}
        campaignId={campaignId}
        submitting={generating}
        onCancel={() => setPickerOpen(false)}
        onConfirm={async (audienceMemberId) => {
          try {
            await onGenerate(audienceMemberId);
          } catch {
            // The failure is shown by `generateError` in this panel.
          } finally {
            setPickerOpen(false);
          }
        }}
      />

      {blockedReason && canGenerate ? <Alert variant="info">{blockedReason}</Alert> : null}
      {generateError ? <Alert variant="error">{errorMessage(generateError)}</Alert> : null}
      {loadError ? <Alert variant="error">{errorMessage(loadError)}</Alert> : null}

      {loading ? (
        <LoadingBlock />
      ) : null}

      {batch?.stale ? (
        <Alert variant="warning">
          <span className="inline-flex items-center gap-1">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            The objective or emails changed after these samples were generated. Generate new
            samples before approving.
          </span>
        </Alert>
      ) : null}

      {batch && !batch.complete && !stalled ? (
        <p className="inline-flex items-center gap-2 text-sm text-sky-700" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Writing samples…
        </p>
      ) : null}
      {stalled ? (
        <Alert variant="warning">
          These samples are taking much longer than expected. The sample writer may be busy
          or unavailable. Try generating new samples, and contact support if this keeps
          happening.
        </Alert>
      ) : null}
      {batch && batch.complete && failed > 0 ? (
        <Alert variant="warning">
          {failed} sample{failed === 1 ? "" : "s"} could not be generated. See the details below.
        </Alert>
      ) : null}
      {batch && batch.all_ok && !batch.stale ? (
        <Alert variant="success">All samples were generated.</Alert>
      ) : null}

      {!batch && !loading ? (
        <p className="text-sm text-slate-500">No samples yet.</p>
      ) : null}

      <div className="space-y-5">
        {groups.map((group) => (
          <div key={group[0].recipient.audience_member_id} className="space-y-2">
            <div>
              <p className="text-sm font-semibold text-slate-900">{recipientName(group[0])}</p>
              <p className="text-xs text-slate-500">
                {[group[0].recipient.title, group[0].recipient.company]
                  .filter(Boolean)
                  .join(" — ")}
              </p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {group.map((item, index) => (
                <SampleCard key={item.id} item={item} emailNumber={index + 1} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
