"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Loader2, Mail, Plus, Timer, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  addSequenceStep,
  deleteSequenceStep,
  getSequence,
  reorderSequenceSteps,
  updateSequenceStep,
} from "@/lib/campaigns-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";
import type { SequenceStep } from "@/types/domain";

const COMMON_VARIABLES = [
  { label: "First name", code: "{{first_name}}" },
  { label: "First name (fallback)", code: "{{first_name|there}}" },
  { label: "Company", code: "{{company}}" },
  { label: "Title", code: "{{title}}" },
];

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function EmailStepEditor({
  step,
  onSave,
  isSaving,
}: {
  step: SequenceStep;
  onSave: (subject: string, bodyHtml: string) => void;
  isSaving: boolean;
}) {
  const [subject, setSubject] = useState(step.email_subject ?? "");
  const [bodyHtml, setBodyHtml] = useState(step.email_body_html ?? "");
  const [dirty, setDirty] = useState(false);

  function insertVariable(code: string) {
    setBodyHtml((prev) => `${prev}${code}`);
    setDirty(true);
  }

  return (
    <div className="space-y-3">
      <Field label="Subject">
        <Input
          value={subject}
          onChange={(e) => {
            setSubject(e.target.value);
            setDirty(true);
          }}
        />
      </Field>
      <Field label="Body (HTML)">
        <textarea
          className="min-h-[120px] w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          value={bodyHtml}
          onChange={(e) => {
            setBodyHtml(e.target.value);
            setDirty(true);
          }}
        />
      </Field>
      <div className="flex flex-wrap gap-1.5">
        {COMMON_VARIABLES.map((v) => (
          <button
            key={v.code}
            type="button"
            onClick={() => insertVariable(v.code)}
            className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 hover:bg-slate-100"
          >
            {v.label}
          </button>
        ))}
      </div>
      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={!dirty || !subject.trim() || isSaving}
          onClick={() => {
            onSave(subject, bodyHtml);
            setDirty(false);
          }}
        >
          {isSaving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
          Save step
        </Button>
      </div>
    </div>
  );
}

function WaitStepEditor({
  step,
  onSave,
  isSaving,
}: {
  step: SequenceStep;
  onSave: (minutes: number) => void;
  isSaving: boolean;
}) {
  const initialDays = step.wait_duration_minutes
    ? Math.round(step.wait_duration_minutes / 1440)
    : 1;
  const [days, setDays] = useState(initialDays);
  const dirty = days * 1440 !== step.wait_duration_minutes;

  return (
    <div className="flex items-end gap-3">
      <Field label="Wait duration (days)">
        <Input
          type="number"
          min={1}
          value={days}
          onChange={(e) => setDays(Math.max(1, Number(e.target.value) || 1))}
          className="w-32"
        />
      </Field>
      <Button
        size="sm"
        disabled={!dirty || isSaving}
        onClick={() => onSave(days * 1440)}
      >
        {isSaving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
        Save
      </Button>
    </div>
  );
}

export default function CampaignSequencePage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const [actionError, setActionError] = useState<string | null>(null);

  const sequenceKey = ["workspace", activeWorkspaceId, "campaigns", campaignId, "sequence"];

  const sequenceQuery = useQuery({
    queryKey: sequenceKey,
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? getSequence(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: sequenceKey });

  const addStepMutation = useMutation({
    mutationFn: (kind: "EMAIL" | "WAIT") => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      const position = (sequenceQuery.data?.steps.length ?? 0) + 1;
      return kind === "EMAIL"
        ? addSequenceStep(activeWorkspaceId, campaignId, {
            kind: "EMAIL",
            position,
            email_subject: "New email",
            email_body_html: "<p>Hi {{first_name|there}},</p>",
          })
        : addSequenceStep(activeWorkspaceId, campaignId, {
            kind: "WAIT",
            position,
            wait_duration_minutes: 1440,
          });
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const updateStepMutation = useMutation({
    mutationFn: (vars: { step: SequenceStep; patch: Record<string, unknown> }) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return updateSequenceStep(activeWorkspaceId, campaignId, vars.step.id, {
        expected_version: vars.step.version,
        ...vars.patch,
      });
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const deleteStepMutation = useMutation({
    mutationFn: (stepId: string) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return deleteSequenceStep(activeWorkspaceId, campaignId, stepId);
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  const reorderMutation = useMutation({
    mutationFn: (steps: { step_id: string; position: number }[]) => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return reorderSequenceSteps(activeWorkspaceId, campaignId, steps);
    },
    onSuccess: invalidate,
    onError: (err) => setActionError(errorMessage(err)),
  });

  function move(step: SequenceStep, direction: -1 | 1) {
    const steps = sequenceQuery.data?.steps ?? [];
    const index = steps.findIndex((s) => s.id === step.id);
    const swapIndex = index + direction;
    if (swapIndex < 0 || swapIndex >= steps.length) return;
    const reordered = steps.map((s) => ({ step_id: s.id, position: s.position }));
    const a = reordered[index];
    const b = reordered[swapIndex];
    reordered[index] = { ...a, position: b.position };
    reordered[swapIndex] = { ...b, position: a.position };
    reorderMutation.mutate(reordered);
  }

  const steps = sequenceQuery.data?.steps ?? [];

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Sequence</h2>
        <p className="text-sm text-slate-500">
          Alternate Email and Wait steps, starting and ending with an Email.
        </p>
      </div>

      {actionError && <Alert variant="error">{actionError}</Alert>}

      {sequenceQuery.isLoading ? (
        <div className="flex min-h-[150px] items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : steps.length === 0 ? (
        <div className="flex min-h-[120px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          No steps yet. Start with an Email step.
        </div>
      ) : (
        <ol className="space-y-3">
          {steps.map((step, i) => (
            <li
              key={step.id}
              className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  {step.kind === "EMAIL" ? (
                    <Mail className="h-4 w-4 text-indigo-500" />
                  ) : (
                    <Timer className="h-4 w-4 text-amber-500" />
                  )}
                  Step {i + 1}: {step.kind === "EMAIL" ? "Email" : "Wait"}
                </div>
                {mayDraft && (
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={i === 0 || reorderMutation.isPending}
                      onClick={() => move(step, -1)}
                    >
                      <ArrowUp className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={i === steps.length - 1 || reorderMutation.isPending}
                      onClick={() => move(step, 1)}
                    >
                      <ArrowDown className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-red-600 hover:text-red-700"
                      onClick={() => deleteStepMutation.mutate(step.id)}
                      disabled={deleteStepMutation.isPending}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
              </div>
              {mayDraft &&
                (step.kind === "EMAIL" ? (
                  <EmailStepEditor
                    step={step}
                    isSaving={updateStepMutation.isPending}
                    onSave={(subject, bodyHtml) =>
                      updateStepMutation.mutate({
                        step,
                        patch: { email_subject: subject, email_body_html: bodyHtml },
                      })
                    }
                  />
                ) : (
                  <WaitStepEditor
                    step={step}
                    isSaving={updateStepMutation.isPending}
                    onSave={(minutes) =>
                      updateStepMutation.mutate({
                        step,
                        patch: { wait_duration_minutes: minutes },
                      })
                    }
                  />
                ))}
              {!mayDraft && step.kind === "EMAIL" && (
                <p className="text-sm text-slate-600">{step.email_subject}</p>
              )}
            </li>
          ))}
        </ol>
      )}

      {mayDraft && (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={addStepMutation.isPending}
            onClick={() => addStepMutation.mutate("EMAIL")}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add Email step
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={addStepMutation.isPending}
            onClick={() => addStepMutation.mutate("WAIT")}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add Wait step
          </Button>
        </div>
      )}
    </div>
  );
}
