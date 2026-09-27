import * as React from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// Cursor pagination footer. The API only exposes a next cursor, so this offers
// "first page" and "next", not page numbers.
export function CursorPagination({
  summary,
  canGoFirst,
  canGoNext,
  onFirst,
  onNext,
  firstLabel = "First page",
  className,
}: {
  summary?: React.ReactNode;
  canGoFirst: boolean;
  canGoNext: boolean;
  onFirst: () => void;
  onNext: () => void;
  firstLabel?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3",
        className,
      )}
    >
      <div className="text-xs text-slate-500">{summary}</div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={!canGoFirst} onClick={onFirst}>
          {firstLabel}
        </Button>
        <Button variant="outline" size="sm" disabled={!canGoNext} onClick={onNext}>
          Next
        </Button>
      </div>
    </div>
  );
}
