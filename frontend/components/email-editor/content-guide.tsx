import { AlertTriangle, CheckCircle2 } from "lucide-react";

import type { PreviewState } from "@/components/email-editor/use-email-preview";

const TIPS = [
  "Keep the subject short (under about 50 characters) and specific to the person.",
  "Add a fallback to variables that might be empty, like {{first_name|there}}, so a gap never shows up in the email.",
  "Use the pre-header (about 40–100 characters) to add context that follows the subject in the inbox.",
  "Ask for one thing. Short, plain-text style emails usually get more replies than heavily designed ones.",
  "Use only a few links and images; many links and large images can hurt deliverability.",
  "Send yourself a test email and check it on mobile before activating the campaign.",
];

type Props = {
  preview: PreviewState;
  stepNumber: number;
};

export function ContentGuide({ preview, stepNumber }: Props) {
  const detected = preview.data?.detected_variables ?? [];
  const missing = preview.data?.missing_variables ?? [];

  return (
    <div className="space-y-5 p-4 text-sm text-slate-700">
      <section aria-labelledby="guide-vars">
        <h3 id="guide-vars" className="mb-2 text-sm font-semibold text-slate-900">
          Personalization in step {stepNumber}
        </h3>
        {preview.error ? (
          <p className="flex items-start gap-2 text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {preview.error}
          </p>
        ) : detected.length === 0 ? (
          <p className="text-slate-500">
            No variables yet. Use the {"{ }"} button to personalize the subject or body.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {detected.map((name) => (
              <li
                key={name}
                className={`rounded border px-2 py-0.5 font-mono text-xs ${
                  missing.includes(name)
                    ? "border-amber-200 bg-amber-50 text-amber-800"
                    : "border-slate-200 bg-slate-50 text-slate-700"
                }`}
              >
                {`{{${name}}}`}
              </li>
            ))}
          </ul>
        )}
        {missing.length > 0 ? (
          <p className="mt-2 flex items-start gap-2 text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>
              The selected prospect has no value for {missing.map((m) => `{{${m}}}`).join(", ")}{" "}
              and no fallback, so it would be blank. Add a fallback, e.g.{" "}
              <code className="rounded bg-slate-100 px-1">{`{{${missing[0]}|there}}`}</code>.
            </span>
          </p>
        ) : detected.length > 0 && !preview.error ? (
          <p className="mt-2 flex items-center gap-2 text-emerald-700">
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            Every variable has a value for the selected prospect.
          </p>
        ) : null}
      </section>

      <section aria-labelledby="guide-tips">
        <h3 id="guide-tips" className="mb-2 text-sm font-semibold text-slate-900">
          Writing tips
        </h3>
        <ul className="list-disc space-y-1.5 pl-5">
          {TIPS.map((tip) => (
            <li key={tip}>{tip}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
