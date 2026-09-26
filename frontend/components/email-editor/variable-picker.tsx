"use client";

import * as React from "react";
import { Braces } from "lucide-react";

import { Popover } from "@/components/ui/popover";
import { TEMPLATE_VARIABLE_GROUPS } from "@/lib/lead-fields";
import { cn } from "@/lib/utils";

const CUSTOM_FIELD_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

// {{first_name}} + "there" -> {{first_name|there}}. Braces/pipes are stripped
// from the fallback so it cannot break the placeholder syntax the server parses.
function withFallback(code: string, fallback: string) {
  const clean = fallback.replace(/[{}|]/g, "").trim();
  if (!clean) return code;
  return code.replace(/\}\}$/, `|${clean}}}`);
}

type Props = {
  onInsert: (code: string) => void;
  label?: string;
  disabled?: boolean;
  className?: string;
};

export function VariablePicker({ onInsert, label = "Insert variable", disabled, className }: Props) {
  const [fallback, setFallback] = React.useState("");
  const [customName, setCustomName] = React.useState("");
  const customValid = CUSTOM_FIELD_RE.test(customName);

  return (
    <Popover
      label={label}
      className="w-72"
      trigger={({ toggle, ariaProps }) => (
        <button
          type="button"
          onClick={toggle}
          disabled={disabled}
          aria-label={label}
          title={label}
          {...ariaProps}
          className={cn(
            "inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:pointer-events-none disabled:opacity-50",
            className,
          )}
        >
          <Braces className="h-4 w-4" aria-hidden="true" />
        </button>
      )}
    >
      {(close) => {
        const insert = (code: string) => {
          onInsert(code);
          close();
        };
        return (
          <div className="max-h-80 space-y-3 overflow-y-auto text-sm">
            <label className="block text-xs font-medium text-slate-600">
              Fallback if empty (optional)
              <input
                value={fallback}
                onChange={(event) => setFallback(event.target.value)}
                placeholder="e.g. there"
                className="mt-1 h-8 w-full rounded-md border border-slate-200 px-2 text-sm"
              />
            </label>
            {TEMPLATE_VARIABLE_GROUPS.map((group) => (
              <div key={group.label}>
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {group.label}
                </p>
                <div className="flex flex-wrap gap-1">
                  {group.variables.map((variable) => (
                    <button
                      key={variable.code}
                      type="button"
                      onClick={() => insert(withFallback(variable.code, fallback))}
                      className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
                    >
                      {variable.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Custom field
              </p>
              <div className="flex gap-1">
                <input
                  value={customName}
                  onChange={(event) => setCustomName(event.target.value)}
                  placeholder="field_name"
                  aria-label="Custom field name"
                  className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 px-2 text-sm"
                />
                <button
                  type="button"
                  disabled={!customValid}
                  onClick={() => insert(withFallback(`{{custom.${customName}}}`, fallback))}
                  className="h-8 rounded-md bg-slate-900 px-3 text-xs font-medium text-white disabled:opacity-40"
                >
                  Insert
                </button>
              </div>
              {customName && !customValid ? (
                <p className="mt-1 text-xs text-red-600">
                  Use letters, numbers and underscores; start with a letter.
                </p>
              ) : null}
            </div>
          </div>
        );
      }}
    </Popover>
  );
}
