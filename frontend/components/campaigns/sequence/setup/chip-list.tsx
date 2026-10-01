"use client";

import * as React from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Props = {
  label: string;
  hint?: string;
  values: string[];
  maxItems: number;
  maxChars: number;
  disabled?: boolean;
  placeholder?: string;
  onChange: (next: string[]) => void;
};

/** A short editable list of phrases (services, industries, key messages). */
export function ChipList({
  label,
  hint,
  values,
  maxItems,
  maxChars,
  disabled = false,
  placeholder = "Type and press Enter",
  onChange,
}: Props) {
  const [text, setText] = React.useState("");
  const [error, setError] = React.useState<string | undefined>();
  const id = React.useId();

  function add() {
    const item = text.trim();
    if (!item) return;
    if (values.some((v) => v.toLowerCase() === item.toLowerCase())) {
      setError("Already added.");
      return;
    }
    if (values.length >= maxItems) {
      setError(`You can add up to ${maxItems}.`);
      return;
    }
    setError(undefined);
    onChange([...values, item.slice(0, maxChars)]);
    setText("");
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-slate-900">
        {label}
      </label>
      {hint ? <p className="text-xs text-slate-500">{hint}</p> : null}
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
            maxLength={maxChars}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                add();
              }
            }}
            placeholder={placeholder}
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
