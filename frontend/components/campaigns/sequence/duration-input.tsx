"use client";

import * as React from "react";

import {
  formatDuration,
  splitDuration,
  toMinutes,
  validateDuration,
  type DurationUnit,
} from "@/components/campaigns/sequence/duration";
import { Select } from "@/components/ui/select";

type Props = {
  minutes: number;
  onSave: (minutes: number) => void;
  onCancel: () => void;
  isSaving?: boolean;
  // e.g. "Step 2" so the sentence reads "Step 2 will be sent about 2 days ..."
  subjectLabel: string;
  previousLabel: string;
};

export function DurationEditor({
  minutes,
  onSave,
  onCancel,
  isSaving,
  subjectLabel,
  previousLabel,
}: Props) {
  const initial = splitDuration(minutes);
  const [value, setValue] = React.useState(String(initial.value));
  const [unit, setUnit] = React.useState<DurationUnit>(initial.unit);
  const parsed = Number(value);
  const error = value.trim() === "" ? "Enter a number." : validateDuration(parsed, unit);

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!error) onSave(toMinutes(parsed, unit));
      }}
    >
      <div className="flex items-end gap-2">
        <label className="text-xs font-medium text-slate-600">
          Wait
          <input
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            aria-invalid={Boolean(error)}
            className="mt-1 block h-9 w-20 rounded-md border border-slate-200 px-2 text-sm"
          />
        </label>
        <label className="text-xs font-medium text-slate-600">
          <span className="sr-only">Unit</span>
          <Select
            aria-label="Unit"
            value={unit}
            onChange={(event) => setUnit(event.target.value as DurationUnit)}
            className="h-9 w-28"
          >
            <option value="minutes">minutes</option>
            <option value="hours">hours</option>
            <option value="days">days</option>
          </Select>
        </label>
      </div>
      {error ? (
        <p role="alert" className="text-xs text-red-600">
          {error}
        </p>
      ) : (
        <p className="text-xs text-slate-500">
          {subjectLabel} will be sent {formatDuration(toMinutes(parsed, unit))} after{" "}
          {previousLabel} is sent (within your sending window).
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="h-8 rounded-md px-3 text-xs font-medium text-slate-600 hover:bg-slate-100"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={Boolean(error) || isSaving}
          className="h-8 rounded-md bg-slate-900 px-3 text-xs font-medium text-white disabled:opacity-40"
        >
          {isSaving ? "Saving…" : "Save wait"}
        </button>
      </div>
    </form>
  );
}
