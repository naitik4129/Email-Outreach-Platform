"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "danger" | "default";
  // Keeps the dialog open and the confirm button busy while the caller's
  // mutation runs; the caller closes it on settle.
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

// Replaces window.confirm with an accessible, on-brand dialog (focus trap,
// Escape, backdrop click all come from Dialog).
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!loading) onCancel();
      }}
      title={title}
      align="center"
      className="max-w-md rounded-xl"
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={tone === "danger" ? "danger" : "primary"}
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      }
    >
      {description ? (
        <div className="px-4 py-4 text-sm leading-6 text-slate-600">{description}</div>
      ) : null}
    </Dialog>
  );
}
