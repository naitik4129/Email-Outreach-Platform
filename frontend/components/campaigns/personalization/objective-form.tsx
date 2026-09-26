"use client";

import * as React from "react";
import { Loader2, Plus, X } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { PersonalizationConfig } from "@/types/domain";

// Mirrors the server schema (PersonalizationConfig). The server validates again;
// this only gives instant feedback.
export const OBJECTIVE_LIMITS = {
  objective: 1000,
  offer: 1000,
  cta: 500,
  target: 500,
  problem_solved: 1000,
  tone: 200,
  phrase: 200,
  phrases: 10,
} as const;

export const EMPTY_OBJECTIVE: PersonalizationConfig = {
  objective: "",
  offer: "",
  cta: "",
  target: "",
  problem_solved: "",
  tone: "",
  must_mention: [],
  never_say: [],
};

type Errors = Partial<Record<keyof PersonalizationConfig, string>>;

export function validateObjective(config: PersonalizationConfig): Errors {
  const errors: Errors = {};
  const required: (keyof typeof OBJECTIVE_LIMITS & keyof PersonalizationConfig)[] = [
    "objective",
    "offer",
    "cta",
  ];
  for (const key of required) {
    if (!config[key].trim()) errors[key] = "This is required.";
  }
  for (const key of ["objective", "offer", "cta", "target", "problem_solved", "tone"] as const) {
    if (config[key].length > OBJECTIVE_LIMITS[key]) {
      errors[key] = `Keep this under ${OBJECTIVE_LIMITS[key]} characters.`;
    }
  }
  return errors;
}

function normalizeConfig(config: PersonalizationConfig): PersonalizationConfig {
  return {
    ...config,
    objective: config.objective.trim(),
    offer: config.offer.trim(),
    cta: config.cta.trim(),
    target: config.target.trim(),
    problem_solved: config.problem_solved.trim(),
    tone: config.tone.trim(),
  };
}

function sameConfig(a: PersonalizationConfig, b: PersonalizationConfig) {
  return JSON.stringify(normalizeConfig(a)) === JSON.stringify(normalizeConfig(b));
}

function PhraseList({
  label,
  hint,
  values,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  values: string[];
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const [text, setText] = React.useState("");
  const [error, setError] = React.useState<string | undefined>();
  const id = React.useId();

  function add() {
    const phrase = text.trim();
    if (!phrase) return;
    if (values.some((v) => v.toLowerCase() === phrase.toLowerCase())) {
      setError("Already added.");
      return;
    }
    if (values.length >= OBJECTIVE_LIMITS.phrases) {
      setError(`You can add up to ${OBJECTIVE_LIMITS.phrases}.`);
      return;
    }
    if (phrase.length > OBJECTIVE_LIMITS.phrase) {
      setError(`Keep each under ${OBJECTIVE_LIMITS.phrase} characters.`);
      return;
    }
    setError(undefined);
    onChange([...values, phrase]);
    setText("");
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-slate-900">
        {label}
      </label>
      <p className="text-xs text-slate-500">{hint}</p>
      {values.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={`${label} list`}>
          {values.map((value) => (
            <li
              key={value}
              className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-800"
            >
              {value}
              {!disabled ? (
                <button
                  type="button"
                  aria-label={`Remove ${value}`}
                  onClick={() => onChange(values.filter((v) => v !== value))}
                  className="rounded-full p-0.5 text-slate-500 hover:bg-slate-200 hover:text-slate-900"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {!disabled ? (
        <div className="flex gap-2">
          <Input
            id={id}
            value={text}
            maxLength={OBJECTIVE_LIMITS.phrase}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
            placeholder="Type a phrase and press Enter"
          />
          <Button type="button" variant="outline" size="sm" onClick={add} className="h-10">
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Add
          </Button>
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-xs font-medium text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const textareaClass =
  "min-h-[84px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:bg-slate-50 disabled:text-slate-600";

type Props = {
  initial: PersonalizationConfig | null;
  readOnly: boolean;
  saving: boolean;
  error: string | null;
  onSave: (config: PersonalizationConfig) => void;
};

export function ObjectiveForm({ initial, readOnly, saving, error, onSave }: Props) {
  const server = initial ?? EMPTY_OBJECTIVE;
  const [config, setConfig] = React.useState<PersonalizationConfig>(server);
  // The server copy the form was last synced to; "dirty" means edits on top of it.
  const [baseline, setBaseline] = React.useState<PersonalizationConfig>(server);
  const [showErrors, setShowErrors] = React.useState(false);

  const configRef = React.useRef(config);
  configRef.current = config;
  const baselineRef = React.useRef(baseline);
  baselineRef.current = baseline;

  // Follow the server copy (after a save or a refetch) but never overwrite edits
  // the user is in the middle of making.
  const serverKey = JSON.stringify(initial);
  React.useEffect(() => {
    const latest = initial ?? EMPTY_OBJECTIVE;
    const localEdits = !sameConfig(configRef.current, baselineRef.current);
    if (!localEdits || sameConfig(configRef.current, latest)) {
      setConfig(latest);
      setBaseline(latest);
      setShowErrors(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverKey]);

  const errors = validateObjective(config);
  const dirty = !sameConfig(config, baseline);
  const set = <K extends keyof PersonalizationConfig>(key: K, value: PersonalizationConfig[K]) =>
    setConfig((prev) => ({ ...prev, [key]: value }));

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (readOnly || saving) return;
    if (Object.keys(errors).length > 0) {
      setShowErrors(true);
      return;
    }
    onSave(normalizeConfig(config));
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 shadow-sm"
      aria-label="Campaign objective"
    >
      <div>
        <h3 className="text-base font-semibold text-slate-900">Campaign objective</h3>
        <p className="mt-1 text-sm text-slate-500">
          Tell us what you are trying to achieve. Every email is written from this and your
          reference emails, so the core offer and call to action stay the same for everyone.
        </p>
      </div>

      {readOnly ? (
        <Alert variant="info">
          The objective can&apos;t be edited now. Duplicate the campaign to make changes.
        </Alert>
      ) : null}
      {error ? <Alert variant="error">{error}</Alert> : null}

      <Field label="Objective" required error={showErrors ? errors.objective : undefined}>
        <textarea
          className={textareaClass}
          value={config.objective}
          onChange={(event) => set("objective", event.target.value)}
          maxLength={OBJECTIVE_LIMITS.objective}
          disabled={readOnly}
          placeholder="Book a demo with SaaS founders who may need better outbound infrastructure."
        />
      </Field>
      <Field label="What you're offering" required error={showErrors ? errors.offer : undefined}>
        <textarea
          className={textareaClass}
          value={config.offer}
          onChange={(event) => set("offer", event.target.value)}
          maxLength={OBJECTIVE_LIMITS.offer}
          disabled={readOnly}
          placeholder="We help SaaS teams automate manual outbound prospecting."
        />
      </Field>
      <Field label="Call to action" required error={showErrors ? errors.cta : undefined}>
        <Input
          value={config.cta}
          onChange={(event) => set("cta", event.target.value)}
          maxLength={OBJECTIVE_LIMITS.cta}
          disabled={readOnly}
          placeholder="Would you be open to a quick conversation?"
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Who you're targeting">
          <Input
            value={config.target}
            onChange={(event) => set("target", event.target.value)}
            maxLength={OBJECTIVE_LIMITS.target}
            disabled={readOnly}
            placeholder="SaaS founders"
          />
        </Field>
        <Field label="Tone">
          <Input
            value={config.tone}
            onChange={(event) => set("tone", event.target.value)}
            maxLength={OBJECTIVE_LIMITS.tone}
            disabled={readOnly}
            placeholder="Friendly and direct"
          />
        </Field>
      </div>
      <Field label="Problem you solve">
        <textarea
          className={textareaClass}
          value={config.problem_solved}
          onChange={(event) => set("problem_solved", event.target.value)}
          maxLength={OBJECTIVE_LIMITS.problem_solved}
          disabled={readOnly}
          placeholder="Sales teams lose hours to manual list building."
        />
      </Field>
      <PhraseList
        label="Must mention"
        hint="Every email will include these phrases."
        values={config.must_mention}
        disabled={readOnly}
        onChange={(next) => set("must_mention", next)}
      />
      <PhraseList
        label="Never say"
        hint="No email will contain these phrases."
        values={config.never_say}
        disabled={readOnly}
        onChange={(next) => set("never_say", next)}
      />

      {!readOnly ? (
        <div className="flex items-center justify-end gap-3">
          {dirty ? <span className="text-xs text-slate-500">Unsaved changes</span> : null}
          <Button type="submit" disabled={saving || !dirty}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Save objective
          </Button>
        </div>
      ) : null}
    </form>
  );
}
