"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { addLeadsToListWithProgress } from "@/lib/bulk-add-to-list";
import { errorMessage } from "@/lib/errors";
import { createLeadList, listLeads } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { LeadStatus } from "@/types/domain";

type CreateListFromFilterDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: { q: string | null; status: LeadStatus | "ALL"; listId: string | null };
};

function defaultName(filters: CreateListFromFilterDialogProps["filters"]) {
  if (filters.q) return `"${filters.q}" leads`;
  if (filters.status === "ARCHIVED") return "Archived leads";
  if (filters.status === "ALL") return "All leads";
  return "Active leads";
}

// Every lead currently returned by the Leads page's filters is collected
// into a brand-new STATIC list -- a one-time snapshot, not a saved filter.
// Membership never recomputes; editing the list later works exactly like
// any other list.
async function collectFilteredLeadIds(
  workspaceId: string,
  filters: CreateListFromFilterDialogProps["filters"],
  onProgress: (found: number) => void,
): Promise<string[]> {
  const ids: string[] = [];
  let cursor: string | null = null;
  do {
    const page = await listLeads(workspaceId, {
      limit: 200,
      cursor,
      q: filters.q,
      status: filters.status,
      list_id: filters.listId,
    });
    ids.push(...page.items.map((lead) => lead.id));
    onProgress(ids.length);
    cursor = page.next_cursor;
  } while (cursor);
  return ids;
}

export function CreateListFromFilterDialog({
  open,
  onOpenChange,
  filters,
}: CreateListFromFilterDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeWorkspaceId } = useWorkspace();
  const { toast } = useToast();
  const [name, setName] = useState(() => defaultName(filters));
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "collecting" | "adding">("idle");
  const [progress, setProgress] = useState({ found: 0, processed: 0, total: 0 });
  const submitting = phase !== "idle";

  function close() {
    setName(defaultName(filters));
    setError(null);
    setPhase("idle");
    setProgress({ found: 0, processed: 0, total: 0 });
    onOpenChange(false);
  }

  async function handleSubmit() {
    if (!activeWorkspaceId || !name.trim()) return;
    setError(null);
    try {
      setPhase("collecting");
      const leadIds = await collectFilteredLeadIds(activeWorkspaceId, filters, (found) =>
        setProgress((p) => ({ ...p, found })),
      );
      const list = await createLeadList(activeWorkspaceId, name.trim());
      setPhase("adding");
      setProgress({ found: leadIds.length, processed: 0, total: leadIds.length });
      const result = await addLeadsToListWithProgress(activeWorkspaceId, list.id, leadIds, {
        onProgress: (p) => setProgress({ found: leadIds.length, processed: p.processed, total: p.total }),
      });
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "lead-lists"] });
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "leads"] });
      toast(
        result.failed === 0
          ? `Created "${list.name}" with ${result.succeeded} lead${result.succeeded === 1 ? "" : "s"}.`
          : `Created "${list.name}" with ${result.succeeded} of ${leadIds.length} leads (${result.failed} failed).`,
        result.failed > 0 ? "error" : undefined,
      );
      onOpenChange(false);
      router.push(`/app/leads/lists/${list.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setPhase("idle");
    }
  }

  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!submitting) close();
      }}
      title="Create list from filter"
      align="center"
      className="max-w-lg"
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={submitting} onClick={close}>
            Cancel
          </Button>
          <Button type="button" disabled={submitting || !name.trim()} onClick={handleSubmit}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Create list
          </Button>
        </div>
      }
    >
      <div className="space-y-4 p-4">
        {error ? <Alert>{error}</Alert> : null}
        <p className="text-sm text-slate-600">
          Creates a new list with a snapshot of every lead currently matching your search and
          filters. It won&apos;t update automatically as leads change later.
        </p>
        <Field id="new-list-name" label="List name">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={submitting}
            required
            autoFocus
          />
        </Field>
        {phase === "collecting" ? (
          <p className="text-sm text-slate-500" aria-live="polite">
            Finding matching leads… {progress.found} found so far.
          </p>
        ) : null}
        {phase === "adding" ? (
          <p className="text-sm text-slate-500" aria-live="polite">
            Adding {progress.processed} of {progress.total} leads…
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
