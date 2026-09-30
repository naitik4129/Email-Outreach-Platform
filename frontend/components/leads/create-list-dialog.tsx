"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { LeadPicker } from "@/components/leads/lead-picker";
import { addLeadsToListWithProgress } from "@/lib/bulk-add-to-list";
import { errorMessage } from "@/lib/errors";
import { createLeadList } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";

type CreateListDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Leads already chosen elsewhere (the Leads page selection). When given, the
  // "pick leads" step is skipped and the dialog only asks for a name.
  preselectedLeadIds?: string[];
  onCreated?: () => void;
};

type Step = "pick" | "name";

export function CreateListDialog({
  open,
  onOpenChange,
  preselectedLeadIds,
  onCreated,
}: CreateListDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeWorkspaceId } = useWorkspace();
  const { toast } = useToast();
  const hasPreselection = Boolean(preselectedLeadIds && preselectedLeadIds.length > 0);

  const [step, setStep] = useState<Step>("pick");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<{ processed: number; total: number } | null>(null);

  // The dialog stays mounted while closed, so start every opening from scratch.
  useEffect(() => {
    if (!open) return;
    setStep(hasPreselection ? "name" : "pick");
    setSelected(new Set(preselectedLeadIds ?? []));
    setSearch("");
    setName("");
    setError(null);
    setSubmitting(false);
    setProgress(null);
    // preselectedLeadIds is read only at the moment of opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function close() {
    onOpenChange(false);
  }

  async function handleCreate() {
    const listName = name.trim();
    if (!activeWorkspaceId || !listName) return;
    setError(null);
    setSubmitting(true);
    try {
      const created = await createLeadList(activeWorkspaceId, listName);
      const leadIds = [...selected];
      let succeeded = 0;
      let failed = 0;
      if (leadIds.length > 0) {
        setProgress({ processed: 0, total: leadIds.length });
        ({ succeeded, failed } = await addLeadsToListWithProgress(
          activeWorkspaceId,
          created.id,
          leadIds,
          { onProgress: (p) => setProgress({ processed: p.processed, total: p.total }) },
        ));
      }
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "lead-lists"] });
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "leads"] });
      toast(
        failed === 0
          ? leadIds.length === 0
            ? `Created "${created.name}".`
            : `Created "${created.name}" with ${succeeded} lead${succeeded === 1 ? "" : "s"}.`
          : `Created "${created.name}" with ${succeeded} of ${leadIds.length} leads (${failed} could not be added).`,
        failed > 0 ? "error" : undefined,
      );
      onCreated?.();
      close();
      router.push(`/app/leads/lists/${created.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setSubmitting(false);
      setProgress(null);
    }
  }

  const count = selected.size;

  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!submitting) close();
      }}
      title={step === "pick" ? "Create list: choose leads" : "Create list: name it"}
      align="center"
      className="max-w-2xl"
      footer={
        step === "pick" ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-slate-500" aria-live="polite">
              {count} selected
            </span>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={close}>
                Cancel
              </Button>
              <Button type="button" onClick={() => setStep("name")}>
                Next
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-slate-500" aria-live="polite">
              {submitting && progress
                ? `Adding ${progress.processed} of ${progress.total}…`
                : `${count} lead${count === 1 ? "" : "s"} selected`}
            </span>
            <div className="flex gap-2">
              {hasPreselection ? (
                <Button type="button" variant="outline" disabled={submitting} onClick={close}>
                  Cancel
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  disabled={submitting}
                  onClick={() => {
                    setError(null);
                    setStep("pick");
                  }}
                >
                  Back
                </Button>
              )}
              <Button
                type="button"
                disabled={submitting || !name.trim()}
                onClick={handleCreate}
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                Create list
              </Button>
            </div>
          </div>
        )
      }
    >
      <div className="space-y-4 p-4">
        {error ? <Alert>{error}</Alert> : null}
        {step === "pick" ? (
          <LeadPicker
            enabled={open}
            search={search}
            onSearchChange={setSearch}
            selected={selected}
            onSelectedChange={setSelected}
          />
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleCreate();
            }}
          >
            <p className="text-sm text-slate-600">
              {count === 0
                ? "No leads selected, so the list will start empty. You can add leads to it later."
                : `${count} lead${count === 1 ? "" : "s"} will be added to the new list.`}
            </p>
            <Field id="new-list-name" label="List name" required>
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={submitting}
                required
                autoFocus
              />
            </Field>
          </form>
        )}
      </div>
    </Dialog>
  );
}
