"use client";

import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  LEAD_PROFILE_FIELDS,
  LEAD_PROFILE_GROUPS,
  type LeadProfileFormValues,
} from "@/lib/lead-fields";
import type { LeadProfile, LeadProfileKey } from "@/types/domain";

type LeadProfileFieldsProps = {
  idPrefix: string;
  values: LeadProfileFormValues;
  onChange: (key: LeadProfileKey, value: string) => void;
  disabled?: boolean;
  defaultOpen?: boolean;
};

// Collapsed by default on create so the form stays short; every field is
// optional and only the server decides whether a value is acceptable.
export function LeadProfileFields({
  idPrefix,
  values,
  onChange,
  disabled,
  defaultOpen = false,
}: LeadProfileFieldsProps) {
  return (
    <details
      className="rounded-md border border-slate-200 md:col-span-2"
      open={defaultOpen}
    >
      <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">
        More details
      </summary>
      <div className="space-y-5 border-t border-slate-200 p-3">
        {LEAD_PROFILE_GROUPS.map((group) => (
          <fieldset key={group}>
            <legend className="text-xs font-semibold uppercase tracking-normal text-slate-500">
              {group}
            </legend>
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              {LEAD_PROFILE_FIELDS.filter((field) => field.group === group).map(
                (field) => (
                  <Field
                    key={field.key}
                    id={`${idPrefix}-${field.key.replace(/_/g, "-")}`}
                    label={field.label}
                  >
                    <Input
                      // Not type="url": browsers reject "example.com" without a
                      // scheme, but the server accepts it and defaults to https.
                      type={field.input === "url" ? "text" : field.input}
                      inputMode={field.input === "url" ? "url" : undefined}
                      value={values[field.key]}
                      onChange={(event) => onChange(field.key, event.target.value)}
                      disabled={disabled}
                      placeholder={field.placeholder}
                      min={field.min}
                      max={field.max}
                    />
                  </Field>
                ),
              )}
            </div>
          </fieldset>
        ))}
      </div>
    </details>
  );
}

// Only http(s) values become links, so a stored value can never render as a
// javascript: href even if it got past server validation.
function isHttpUrl(value: string) {
  return /^https?:\/\//i.test(value);
}

function DetailValue({ value, isUrl }: { value: string; isUrl: boolean }) {
  if (isUrl && isHttpUrl(value)) {
    return (
      <a
        href={value}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-teal-700 hover:underline"
      >
        {value}
      </a>
    );
  }
  return <span className="break-words">{value}</span>;
}

export function LeadProfileDetails({ lead }: { lead: LeadProfile }) {
  const groups = LEAD_PROFILE_GROUPS.map((group) => ({
    group,
    fields: LEAD_PROFILE_FIELDS.filter(
      (field) =>
        field.group === group &&
        lead[field.key] !== null &&
        lead[field.key] !== undefined &&
        lead[field.key] !== "",
    ),
  })).filter(({ fields }) => fields.length > 0);

  if (groups.length === 0) {
    return <p className="mt-4 text-sm text-slate-500">No additional details.</p>;
  }

  return (
    <div className="mt-5 space-y-5">
      {groups.map(({ group, fields }) => (
        <section key={group}>
          <h3 className="text-xs font-semibold uppercase tracking-normal text-slate-500">
            {group}
          </h3>
          <dl className="mt-3 grid gap-4 text-sm md:grid-cols-2">
            {fields.map((field) => (
              <div key={field.key}>
                <dt className="font-medium text-slate-500">{field.label}</dt>
                <dd className="mt-1 text-slate-950">
                  <DetailValue
                    value={String(lead[field.key])}
                    isUrl={field.input === "url"}
                  />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
