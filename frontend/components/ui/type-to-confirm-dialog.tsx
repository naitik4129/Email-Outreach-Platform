"use client";

import * as React from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

type TypeToConfirmDialogProps = {
  open: boolean;
  title: string;
  // What will happen and what will not, in plain words.
  description: React.ReactNode;
  // The exact text the user has to type (a name, an email address, ...).
  phrase: string;
  confirmLabel: string;
  loading?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
};

// For irreversible actions (permanent delete, erasure): the confirm button stays
// disabled until the item's name is typed. The server compares the same text again,
// so this is a guard against slips, not the security boundary.
export function TypeToConfirmDialog({
  open,
  title,
  description,
  phrase,
  confirmLabel,
  loading = false,
  error,
  onConfirm,
  onCancel,
}: TypeToConfirmDialogProps) {
  const [typed, setTyped] = React.useState("");
  const inputId = React.useId();

  React.useEffect(() => {
    if (open) setTyped("");
  }, [open]);

  const matches = typed.trim() === phrase.trim() && phrase.trim().length > 0;

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
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            loading={loading}
            disabled={!matches}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      }
    >
      <form
        className="space-y-3 px-4 py-4 text-sm leading-6 text-slate-600"
        onSubmit={(event) => {
          event.preventDefault();
          if (matches && !loading) onConfirm();
        }}
      >
        <div>{description}</div>
        <div className="space-y-1.5">
          <label htmlFor={inputId} className="block text-sm font-medium text-slate-800">
            Type <span className="font-semibold text-slate-900">{phrase}</span> to confirm
          </label>
          <Input
            id={inputId}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={loading}
          />
        </div>
        {error ? <Alert variant="error">{error}</Alert> : null}
      </form>
    </Dialog>
  );
}
