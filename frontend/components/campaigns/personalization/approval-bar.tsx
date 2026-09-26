"use client";

import { CheckCircle2, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { PersonalizationApproval, PreviewBatch } from "@/types/domain";

type Props = {
  approval: PersonalizationApproval;
  batch: PreviewBatch | null | undefined;
  // campaigns.execute -- the same role that may activate the campaign. UX only;
  // the server and database enforce it.
  canApprove: boolean;
  // The campaign is no longer a draft.
  readOnly: boolean;
  approving: boolean;
  error: string | null;
  onApprove: () => void;
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

// Why the Approve button is unavailable (null = it can be pressed).
export function approvalBlockedReason(
  approval: PersonalizationApproval,
  batch: PreviewBatch | null | undefined,
): string | null {
  if (approval.status === "APPROVED") return "These emails are already approved.";
  if (!batch) return "Generate sample emails first.";
  if (batch.stale) return "The objective or emails changed. Generate new samples.";
  if (!batch.complete) return "Samples are still being generated.";
  if (!batch.all_ok) return "Some samples failed. Fix the problem and generate again.";
  return null;
}

export function ApprovalBar({
  approval,
  batch,
  canApprove,
  readOnly,
  approving,
  error,
  onApprove,
}: Props) {
  const blocked = approvalBlockedReason(approval, batch);

  return (
    <section
      aria-label="Sample approval"
      className="space-y-3 rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
    >
      <div className="flex items-start gap-3">
        {approval.status === "APPROVED" ? (
          <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-600" aria-hidden="true" />
        ) : (
          <ShieldAlert
            className={`mt-0.5 h-5 w-5 ${approval.status === "STALE" ? "text-amber-600" : "text-slate-400"}`}
            aria-hidden="true"
          />
        )}
        <div>
          <h3 className="text-base font-semibold text-slate-900">
            {approval.status === "APPROVED"
              ? "Approved"
              : approval.status === "STALE"
                ? "Approval out of date"
                : "Not approved yet"}
          </h3>
          <p className="mt-1 text-sm text-slate-600">
            {approval.status === "APPROVED"
              ? `Approved${approval.approved_at ? ` on ${formatDate(approval.approved_at)}` : ""}. Changing the objective or any reference email will require a new approval.`
              : approval.status === "STALE"
                ? "The objective or a reference email changed since the samples were approved. Generate new samples and approve them again."
                : "Review the sample emails above. Once you approve them, the campaign can be launched; the rest of the emails are then written automatically just before they send."}
          </p>
        </div>
      </div>

      {error ? <Alert variant="error">{error}</Alert> : null}

      {readOnly ? null : canApprove ? (
        <div className="flex items-center justify-end gap-3">
          {blocked && approval.status !== "APPROVED" ? (
            <span className="text-xs text-slate-500">{blocked}</span>
          ) : null}
          <Button onClick={onApprove} disabled={Boolean(blocked) || approving}>
            {approving ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            )}
            Approve samples
          </Button>
        </div>
      ) : (
        <p className="text-xs text-slate-500">
          Only Managers and above can approve samples. Ask a manager to review this page.
        </p>
      )}
    </section>
  );
}
