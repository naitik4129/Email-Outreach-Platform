"use client";

import * as React from "react";
import { Loader2, Sparkles } from "lucide-react";

import { describeFailureCode } from "@/components/campaigns/personalization/failure-codes";
import type { ReferenceGeneration } from "@/components/campaigns/sequence/use-reference-generation";
import { describeWarnings } from "@/components/campaigns/sequence/setup/setup-copy";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Select } from "@/components/ui/select";

type Props = {
  emailCount: number;
  readOnly: boolean;
  // The campaign setup (objective) is saved, so there is something to write from.
  hasObjective: boolean;
  aiAvailable: boolean;
  generation: ReferenceGeneration;
  // Set when a single email is waiting for the user to confirm regenerating it.
  confirmStep: { id: string; number: number } | null;
  onConfirmStepDone: () => void;
};

const FOLLOW_UP_OPTIONS = [1, 2, 3, 4, 5];
// Matches the API limit on `instructions`.
const MAX_CHANGE_REQUEST = 1000;

/**
 * Writes the reference emails with AI: the whole sequence (asking how many
 * follow-ups when there are no emails yet), with a confirmation before anything
 * is overwritten, inline errors, and Retry.
 */
export function AiDraftBar({
  emailCount,
  readOnly,
  hasObjective,
  aiAvailable,
  generation,
  confirmStep,
  onConfirmStepDone,
}: Props) {
  const [followUps, setFollowUps] = React.useState(2);
  const [confirmAll, setConfirmAll] = React.useState(false);
  // Optional note for the single email being regenerated; cleared on every close
  // so one email's request never carries over to the next.
  const [changes, setChanges] = React.useState("");
  if (!aiAvailable || readOnly) return null;

  const closeStepDialog = () => {
    setChanges("");
    onConfirmStepDone();
  };

  const { isPending, failure, succeeded, warnings } = generation;
  const empty = emailCount === 0;

  return (
    <section
      aria-label="Write emails with AI"
      className="space-y-3 rounded-xl border border-violet-200 bg-violet-50/40 p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
            <Sparkles className="h-4 w-4 text-violet-600" aria-hidden="true" />
            {empty ? "Write your emails with AI" : "Emails written by AI"}
          </h3>
          <p className="text-sm text-slate-600">
            {empty
              ? "We'll write the first email and your follow-ups from your setup. You can edit any of them."
              : "Regenerate every email, or regenerate one from its card. Your edits are replaced."}
          </p>
        </div>

        {!hasObjective ? null : empty ? (
          <div className="flex items-end gap-2">
            <label className="text-xs font-medium text-slate-700">
              Follow-ups
              <Select
                value={followUps}
                disabled={isPending}
                onChange={(event) => setFollowUps(Number(event.target.value))}
                className="mt-1 h-9 w-24"
                aria-label="How many follow-ups?"
              >
                {FOLLOW_UP_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </label>
            <Button
              type="button"
              loading={isPending}
              onClick={() => generation.generate({ scope: "ALL", follow_up_count: followUps })}
            >
              Generate emails
            </Button>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            loading={isPending}
            onClick={() => setConfirmAll(true)}
          >
            Regenerate all emails
          </Button>
        )}
      </div>

      {!hasObjective ? (
        <Alert variant="info">Save your setup first, then we can write your emails.</Alert>
      ) : null}

      {isPending ? (
        <p role="status" className="flex items-center gap-2 text-sm text-slate-600">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Writing your emails… this can take up to a minute.
        </p>
      ) : null}

      {failure ? (
        <Alert variant="error">
          <p>{failure.message}</p>
          {failure.codes.length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {failure.codes.map((code) => (
                <li key={code}>{describeFailureCode(code)}</li>
              ))}
            </ul>
          ) : null}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={generation.retry}>
              Try again
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={generation.dismiss}>
              Dismiss
            </Button>
          </div>
          {failure.count >= 3 ? (
            <p className="mt-2 text-xs">
              Still not working? You can write the emails yourself: open a step with Edit, or add
              one below.
            </p>
          ) : null}
        </Alert>
      ) : null}

      {succeeded && !failure ? (
        <Alert variant="success">
          Your emails are ready. Review them below, then generate samples and approve them.
        </Alert>
      ) : null}
      {describeWarnings(warnings).map((text) => (
        <Alert key={text} variant="warning">
          {text}
        </Alert>
      ))}

      <ConfirmDialog
        open={confirmAll}
        title="Replace all your emails?"
        description={`This replaces the content of all ${emailCount} email${emailCount === 1 ? "" : "s"} with new emails written by AI. Your current text will be lost.`}
        confirmLabel="Replace emails"
        tone="danger"
        onConfirm={() => {
          setConfirmAll(false);
          generation.generate({ scope: "ALL" });
        }}
        onCancel={() => setConfirmAll(false)}
      />
      <ConfirmDialog
        open={confirmStep !== null}
        title={`Regenerate email ${confirmStep?.number ?? ""}?`}
        description={
          <div className="space-y-3">
            <p>
              This replaces the subject and text of this email with a new one written by AI. Your
              current text will be lost.
            </p>
            <label className="block text-xs font-medium text-slate-700">
              What should change? (optional)
              <textarea
                value={changes}
                maxLength={MAX_CHANGE_REQUEST}
                rows={3}
                onChange={(event) => setChanges(event.target.value)}
                placeholder="For example: make it shorter, friendlier, and lead with the pricing offer."
                className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-normal text-slate-900 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-200"
              />
            </label>
          </div>
        }
        confirmLabel="Regenerate"
        tone="danger"
        onConfirm={() => {
          const target = confirmStep;
          const instructions = changes.trim();
          closeStepDialog();
          if (target) {
            generation.generate({
              scope: "STEP",
              step_id: target.id,
              ...(instructions ? { instructions } : {}),
            });
          }
        }}
        onCancel={closeStepDialog}
      />
    </section>
  );
}
