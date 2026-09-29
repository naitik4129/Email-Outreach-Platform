"use client";

import { Dialog } from "@/components/ui/dialog";
import { NewImportPageClient } from "@/app/app/leads/imports/new/new-import-page-client";
import type { ImportJob, ImportKind } from "@/types/domain";

type ImportLeadsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultImportKind?: ImportKind;
  defaultListId?: string;
  // Lets a caller (e.g. a list detail page) react to the finished import
  // instead of navigating away from the page it was opened from.
  onImported?: (job: ImportJob) => void;
};

// Thin modal wrapper around the existing upload/map/confirm wizard so Leads
// and Lists can launch it as a contextual action instead of navigating to
// the standalone /app/leads/imports/new route (which still exists and keeps
// working unchanged for direct links).
export function ImportLeadsDialog({
  open,
  onOpenChange,
  defaultImportKind,
  defaultListId,
  onImported,
}: ImportLeadsDialogProps) {
  return (
    <Dialog
      open={open}
      onRequestClose={() => onOpenChange(false)}
      title="Import leads or suppressions"
      className="max-w-3xl"
    >
      <div className="p-1">
        <NewImportPageClient
          defaultImportKind={defaultImportKind}
          defaultListId={defaultListId}
          showHeading={false}
          onImported={(job) => {
            onOpenChange(false);
            onImported?.(job);
          }}
        />
      </div>
    </Dialog>
  );
}
