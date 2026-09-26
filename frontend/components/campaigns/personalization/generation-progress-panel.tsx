"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

import { describeFailureCode } from "@/components/campaigns/personalization/failure-codes";
import { getGenerationProgress } from "@/lib/personalization-api";
import type { GenerationProgress } from "@/types/domain";

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}

export function GenerationProgressView({ progress }: { progress: GenerationProgress }) {
  const done = progress.succeeded + progress.failed + progress.superseded;
  const failures = Object.entries(progress.failure_codes);
  const capNear =
    progress.budget.generation_cap > 0 &&
    progress.budget.generation_used >= progress.budget.generation_cap;

  return (
    <section
      aria-label="Email generation progress"
      className={`space-y-3 rounded-lg border p-4 ${
        progress.failed > 0 ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"
      }`}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        {progress.pending > 0 ? (
          <Loader2 className="h-4 w-4 animate-spin text-sky-600" aria-hidden="true" />
        ) : progress.failed > 0 ? (
          <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden="true" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
        )}
        Personalized emails
      </div>
      <p className="text-xs text-slate-500">
        Each email is written shortly before it is due to be sent, so the counts below grow over
        time.
      </p>
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-slate-500">Waiting</dt>
          <dd className="font-semibold text-slate-900" data-testid="pending">
            {progress.pending}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Written</dt>
          <dd className="font-semibold text-slate-900" data-testid="succeeded">
            {progress.succeeded}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Sent as standard</dt>
          <dd className="font-semibold text-slate-900" data-testid="fallback">
            {progress.fallback}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-slate-500">Not sent (failed)</dt>
          <dd
            className={`font-semibold ${progress.failed > 0 ? "text-red-700" : "text-slate-900"}`}
            data-testid="failed"
          >
            {progress.failed}
          </dd>
        </div>
      </dl>
      {progress.oldest_pending_at ? (
        <p className="text-xs text-slate-500">
          Next email is due to be written from {formatDateTime(progress.oldest_pending_at)}.
        </p>
      ) : null}
      {failures.length > 0 ? (
        <div>
          <p className="text-xs font-medium text-red-800">
            These emails were not sent because a safe, valid email could not be written:
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-red-800">
            {failures.map(([code, count]) => (
              <li key={code}>
                {count} × {describeFailureCode(code)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {capNear ? (
        <p className="text-xs text-amber-800">
          The daily writing limit ({progress.budget.generation_cap}) has been reached; remaining
          emails continue tomorrow.
        </p>
      ) : (
        <p className="text-xs text-slate-500">
          Today: {progress.budget.generation_used} / {progress.budget.generation_cap} emails
          written{done > 0 ? ` · ${done} finished in total` : ""}.
        </p>
      )}
    </section>
  );
}

type Props = {
  workspaceId: string;
  campaignId: string;
  // Poll only while the campaign is running; a draft has nothing to generate.
  active: boolean;
};

export function GenerationProgressPanel({ workspaceId, campaignId, active }: Props) {
  const query = useQuery({
    queryKey: ["workspace", workspaceId, "campaigns", campaignId, "personalization", "progress"],
    queryFn: () => getGenerationProgress(workspaceId, campaignId),
    enabled: active,
    refetchInterval: 15000,
  });
  if (!active || !query.data) return null;
  return <GenerationProgressView progress={query.data} />;
}
