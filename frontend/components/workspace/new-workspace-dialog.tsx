"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { useWorkspace } from "@/lib/workspace-context";
import { createWorkspace } from "@/lib/workspaces-api";

type NewWorkspaceDialogProps = {
  open: boolean;
  onRequestClose: () => void;
};

export function NewWorkspaceDialog({ open, onRequestClose }: NewWorkspaceDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { switchWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Stable across retries of the *same* submission attempt, so a network
  // retry or an accidental double submit reuses one idempotency key instead
  // of creating two workspaces (mirrors onboarding/workspace/page.tsx).
  const idempotencyKeyRef = useRef<string>(crypto.randomUUID());

  function handleClose() {
    if (submitting) return;
    setName("");
    setError(null);
    onRequestClose();
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (submitting || !trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      const workspace = await createWorkspace(trimmed, idempotencyKeyRef.current);
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      switchWorkspace(workspace.id);
      setName("");
      idempotencyKeyRef.current = crypto.randomUUID();
      onRequestClose();
      router.push("/app/dashboard");
    } catch (err) {
      idempotencyKeyRef.current = crypto.randomUUID();
      setError(
        err instanceof ApiError
          ? "We couldn't create your workspace. Please try again."
          : "Network error. Please check your connection and try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onRequestClose={handleClose}
      title="New Workspace"
      align="center"
      className="max-w-md rounded-xl"
    >
      <form className="space-y-4 p-4" onSubmit={handleSubmit} noValidate>
        {error ? <Alert>{error}</Alert> : null}
        <Field id="new-workspace-name" label="Workspace name">
          <Input
            placeholder="Acme Outreach"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={submitting}
            autoFocus
          />
        </Field>
        <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
          <Button type="button" variant="outline" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={submitting || !name.trim()}>
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              "Create workspace"
            )}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
