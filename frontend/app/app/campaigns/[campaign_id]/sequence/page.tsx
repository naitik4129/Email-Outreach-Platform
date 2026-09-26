"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { computeTimings } from "@/components/campaigns/sequence/duration";
import { EmailStepDialog } from "@/components/campaigns/sequence/email-step-dialog";
import { SequenceTimeline } from "@/components/campaigns/sequence/sequence-timeline";
import { Alert } from "@/components/ui/alert";
import { ApiError } from "@/lib/api-client";
import {
  addSequenceStep,
  deleteSequenceStep,
  duplicateSequenceStep,
  getCampaign,
  getSequence,
  listCampaignMailboxes,
  listCampaignSettings,
  reorderSequenceSteps,
  updateSequenceStep,
} from "@/lib/campaigns-api";
import { canDraftCampaign, canExecuteCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { CampaignSequence, CampaignSettings, SequenceStep } from "@/types/domain";

// Wait inserted before a newly added email; two days is the common cadence.
const DEFAULT_WAIT_MINUTES = 2 * 24 * 60;
const NEW_EMAIL_SUBJECT = "New email";
const NEW_EMAIL_BODY = "<p>Hi {{first_name|there}},</p><p></p>";

const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn’t complete that request. Please try again.";
}

function describeSchedule(settings: CampaignSettings | null): string | null {
  if (!settings) return null;
  const days = [...settings.weekdays]
    .sort((a, b) => a - b)
    .map((d) => WEEKDAY_NAMES[d - 1])
    .filter(Boolean)
    .join(", ");
  return `${days}, ${settings.window_start_local.slice(0, 5)}–${settings.window_end_local.slice(0, 5)} ${settings.timezone}`;
}

export default function CampaignSequencePage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openStepId, setOpenStepId] = useState<string | null>(null);
  const [savingWaitId, setSavingWaitId] = useState<string | null>(null);

  const campaignKey = ["workspace", activeWorkspaceId, "campaigns", campaignId];
  const sequenceKey = [...campaignKey, "sequence"];
  const ready = Boolean(activeWorkspaceId && campaignId);

  const campaignQuery = useQuery({
    queryKey: campaignKey,
    queryFn: () => getCampaign(activeWorkspaceId as string, campaignId),
    enabled: ready,
  });

  const sequenceQuery = useQuery({
    queryKey: sequenceKey,
    queryFn: () => getSequence(activeWorkspaceId as string, campaignId),
    enabled: ready,
  });

  const mailboxesQuery = useQuery({
    queryKey: [...campaignKey, "mailboxes"],
    queryFn: () => listCampaignMailboxes(activeWorkspaceId as string, campaignId),
    enabled: ready,
  });

  const settingsQuery = useQuery({
    queryKey: [...campaignKey, "settings"],
    queryFn: () => listCampaignSettings(activeWorkspaceId as string, campaignId),
    enabled: ready,
  });

  const sequence = sequenceQuery.data;
  const steps = sequence?.steps ?? [];
  const campaignStatus = campaignQuery.data?.status;
  // Editing is allowed only while the campaign is a DRAFT (the server enforces
  // this too and answers state_conflict otherwise).
  const readOnly =
    !mayDraft || campaignStatus !== "DRAFT" || sequence?.status === "FROZEN";
  const latestSettings = settingsQuery.data?.length
    ? settingsQuery.data.reduce((a, b) => (b.revision > a.revision ? b : a))
    : null;

  function afterChange(next?: CampaignSequence) {
    setActionError(null);
    if (next) queryClient.setQueryData(sequenceKey, next);
    void queryClient.invalidateQueries({ queryKey: sequenceKey });
    // The campaign header shows "N items left before ready" from preflight.
    void queryClient.invalidateQueries({ queryKey: [...campaignKey, "preflight"] });
  }

  function onMutationError(error: unknown) {
    setActionError(errorMessage(error));
    if (error instanceof ApiError && error.code === "state_conflict") {
      void queryClient.invalidateQueries({ queryKey: campaignKey });
    }
    void queryClient.invalidateQueries({ queryKey: sequenceKey });
  }

  const addMutation = useMutation({
    mutationFn: async (input: { position: number; withWait: boolean }) =>
      addSequenceStep(activeWorkspaceId as string, campaignId, {
        kind: "EMAIL",
        position: input.position,
        email_subject: NEW_EMAIL_SUBJECT,
        email_body_html: NEW_EMAIL_BODY,
        // Server inserts the wait and the email in one transaction.
        leading_wait_minutes: input.withWait ? DEFAULT_WAIT_MINUTES : undefined,
      }),
    onSuccess: (created) => {
      afterChange();
      setOpenStepId(created.id);
    },
    onError: onMutationError,
  });

  const duplicateMutation = useMutation({
    mutationFn: (step: SequenceStep) =>
      duplicateSequenceStep(activeWorkspaceId as string, campaignId, step.id),
    onSuccess: (next) => afterChange(next),
    onError: onMutationError,
  });

  const deleteMutation = useMutation({
    mutationFn: (step: SequenceStep) =>
      deleteSequenceStep(activeWorkspaceId as string, campaignId, step.id, {
        withAdjacentWait: step.kind === "EMAIL",
      }),
    onSuccess: () => afterChange(),
    onError: onMutationError,
  });

  const reorderMutation = useMutation({
    mutationFn: (ordering: { step_id: string; position: number }[]) =>
      reorderSequenceSteps(activeWorkspaceId as string, campaignId, ordering),
    onSuccess: (next) => afterChange(next),
    onError: onMutationError,
  });

  const waitMutation = useMutation({
    mutationFn: (input: { step: SequenceStep; minutes: number }) =>
      updateSequenceStep(activeWorkspaceId as string, campaignId, input.step.id, {
        expected_version: input.step.version,
        wait_duration_minutes: input.minutes,
      }),
    onMutate: (input) => setSavingWaitId(input.step.id),
    onSuccess: () => afterChange(),
    onError: onMutationError,
    onSettled: () => setSavingWaitId(null),
  });

  const busy =
    addMutation.isPending ||
    duplicateMutation.isPending ||
    deleteMutation.isPending ||
    reorderMutation.isPending;

  function onDelete(step: SequenceStep) {
    const label = step.kind === "EMAIL" ? "this email step and the wait next to it" : "this wait";
    if (!window.confirm(`Delete ${label}?`)) return;
    deleteMutation.mutate(step);
  }

  // Emails swap slots; the waits between slots stay where they are, so the
  // Email/Wait alternation is preserved and only the email content moves.
  function onMoveEmail(step: SequenceStep, direction: -1 | 1) {
    const emails = steps.filter((s) => s.kind === "EMAIL");
    const index = emails.findIndex((s) => s.id === step.id);
    const other = emails[index + direction];
    if (!other) return;
    reorderMutation.mutate(
      steps.map((s) => ({
        step_id: s.id,
        position:
          s.id === step.id ? other.position : s.id === other.id ? step.position : s.position,
      })),
    );
  }

  const openStep = openStepId ? steps.find((s) => s.id === openStepId) : undefined;
  const timings = computeTimings(steps);
  const emailSteps = steps.filter((s) => s.kind === "EMAIL");

  async function refetchOpenStep() {
    const latest = await queryClient.fetchQuery({
      queryKey: sequenceKey,
      queryFn: () => getSequence(activeWorkspaceId as string, campaignId),
      staleTime: 0,
    });
    const index = latest.steps.findIndex((s) => s.id === openStepId);
    const current = index >= 0 ? latest.steps[index] : null;
    const before = index > 0 ? latest.steps[index - 1] : null;
    return { step: current, wait: before && before.kind === "WAIT" ? before : null };
  }

  let dialog = null;
  if (openStep && openStep.kind === "EMAIL" && activeWorkspaceId) {
    const index = steps.findIndex((s) => s.id === openStep.id);
    const previous = index > 0 ? steps[index - 1] : null;
    const precedingWait = previous && previous.kind === "WAIT" ? previous : null;
    const previousEmail = [...steps.slice(0, index)].reverse().find((s) => s.kind === "EMAIL");
    dialog = (
      <EmailStepDialog
        // Remount per step so each open starts from that step's saved state.
        key={openStep.id}
        open
        workspaceId={activeWorkspaceId}
        campaignId={campaignId}
        step={openStep}
        stepNumber={emailSteps.findIndex((s) => s.id === openStep.id) + 1}
        day={timings.get(openStep.id)?.day ?? 1}
        previousEmailDay={previousEmail ? (timings.get(previousEmail.id)?.day ?? 1) : null}
        precedingWait={precedingWait}
        readOnly={readOnly}
        canTestSend={canExecuteCampaign(activeWorkspace?.role_code)}
        mailboxes={(mailboxesQuery.data ?? []).filter((mb) => mb.active)}
        onClose={() => setOpenStepId(null)}
        onSaved={() => afterChange()}
        onRefetch={refetchOpenStep}
        onLocked={() => {
          void queryClient.invalidateQueries({ queryKey: campaignKey });
          void queryClient.invalidateQueries({ queryKey: sequenceKey });
        }}
      />
    );
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Sequence</h2>
        <p className="text-sm text-slate-500">
          Each email is sent to every prospect in the audience. Waits set how long after the
          previous email a follow-up goes out.
        </p>
      </div>

      {readOnly && campaignStatus && campaignStatus !== "DRAFT" ? (
        <Alert variant="info">
          This campaign is {campaignStatus.toLowerCase()}, so its sequence is read-only. Duplicate
          the campaign to make changes.
        </Alert>
      ) : null}
      {actionError ? <Alert variant="error">{actionError}</Alert> : null}
      {sequenceQuery.isError ? (
        <Alert variant="error">{errorMessage(sequenceQuery.error)}</Alert>
      ) : null}

      {sequenceQuery.isLoading ? (
        <div className="flex min-h-[150px] items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" aria-label="Loading" />
        </div>
      ) : steps.length === 0 ? (
        <div className="flex min-h-[160px] flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          <p>No steps yet. Start with an email.</p>
          {!readOnly ? (
            <button
              type="button"
              onClick={() => addMutation.mutate({ position: 1, withWait: false })}
              disabled={addMutation.isPending}
              className="inline-flex h-8 items-center rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-900 hover:bg-slate-100 disabled:opacity-50"
            >
              Add email step
            </button>
          ) : null}
        </div>
      ) : (
        <SequenceTimeline
          steps={steps}
          readOnly={readOnly}
          busy={busy}
          scheduleNote={describeSchedule(latestSettings)}
          savingWaitId={savingWaitId}
          onOpenEmail={(step) => setOpenStepId(step.id)}
          onDuplicate={(step) => duplicateMutation.mutate(step)}
          onDelete={onDelete}
          onMoveEmail={onMoveEmail}
          onSaveWait={(step, minutes) => waitMutation.mutate({ step, minutes })}
          onInsertAfter={(step) =>
            addMutation.mutate({ position: step.position + 1, withWait: true })
          }
          onAddEmail={() =>
            addMutation.mutate({ position: steps.length + 1, withWait: steps.length > 0 })
          }
        />
      )}

      {dialog}
    </div>
  );
}
