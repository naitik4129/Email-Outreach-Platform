"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Popover } from "@/components/ui/popover";
import { ApiError } from "@/lib/api-client";
import { createTemplate } from "@/lib/templates-api";

type Props = {
  workspaceId: string;
  subject: string;
  preheader: string;
  bodyHtml: string;
  disabled?: boolean;
  // Shown as a tooltip when the button is disabled for a reason the user can fix.
  disabledReason?: string;
};

// Saves the current (unsaved-to-the-step) content as a reusable template. The
// step itself is untouched: templates are copied into steps, never linked.
export function SaveAsTemplate({
  workspaceId,
  subject,
  preheader,
  bodyHtml,
  disabled,
  disabledReason,
}: Props) {
  const queryClient = useQueryClient();
  const [name, setName] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [savedName, setSavedName] = React.useState<string | null>(null);

  async function submit(close: () => void) {
    const clean = name.trim();
    if (!clean) {
      setError("Give the template a name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await createTemplate(workspaceId, {
        name: clean,
        subject: subject.trim(),
        body_html: bodyHtml,
        preheader: preheader.trim() || null,
      });
      setSavedName(clean);
      setName("");
      void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "templates"] });
      close();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "We couldn’t save the template. Try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {savedName ? (
        <span className="flex items-center gap-1 text-xs text-emerald-700" role="status">
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
          Saved as &ldquo;{savedName}&rdquo;
        </span>
      ) : null}
      <Popover
        label="Save as template"
        align="end"
        className="bottom-full mb-1 mt-0 w-72"
        trigger={({ toggle, ariaProps }) => (
          <span title={disabled ? disabledReason : undefined}>
            <Button variant="ghost" size="sm" onClick={toggle} disabled={disabled} {...ariaProps}>
              Save as template
            </Button>
          </span>
        )}
      >
        {(close) => (
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              void submit(close);
            }}
          >
            <label className="block text-xs font-medium text-slate-600">
              Template name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={200}
                className="mt-1 h-8 w-full rounded-md border border-slate-200 px-2 text-sm"
              />
            </label>
            {error ? (
              <p role="alert" className="text-xs text-red-600">
                {error}
              </p>
            ) : null}
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={saving}
                className="inline-flex h-8 items-center gap-1 rounded-md bg-slate-900 px-3 text-xs font-medium text-white disabled:opacity-50"
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> : null}
                Save template
              </button>
            </div>
          </form>
        )}
      </Popover>
    </div>
  );
}
