"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";

// Shown while rows are selected: the count, the bulk actions, and a way out.
export function SelectionBar({
  count,
  onClear,
  children,
}: {
  count: number;
  onClear: () => void;
  children: React.ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className="flex flex-wrap items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm"
    >
      <span className="font-medium text-brand-900" aria-live="polite">
        {count} selected
      </span>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
      <Button type="button" variant="ghost" size="sm" className="ml-auto" onClick={onClear}>
        Clear selection
      </Button>
    </div>
  );
}
