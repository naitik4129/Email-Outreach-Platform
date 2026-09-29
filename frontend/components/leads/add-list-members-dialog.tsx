"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { addLeadsToListWithProgress } from "@/lib/bulk-add-to-list";
import { errorMessage } from "@/lib/errors";
import { listLeads } from "@/lib/leads-api";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useWorkspace } from "@/lib/workspace-context";

type AddListMembersDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  listId: string;
  listName: string;
};

function fullName(firstName: string | null, lastName: string | null) {
  return [firstName, lastName].filter(Boolean).join(" ") || "Unnamed lead";
}

export function AddListMembersDialog({
  open,
  onOpenChange,
  listId,
  listName,
}: AddListMembersDialogProps) {
  const queryClient = useQueryClient();
  const { activeWorkspaceId } = useWorkspace();
  const { toast } = useToast();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ processed: number; total: number } | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const debouncedSearch = useDebouncedValue(search, 350);

  const searchQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "leads", "list-picker", debouncedSearch],
    queryFn: () =>
      listLeads(activeWorkspaceId!, { limit: 25, q: debouncedSearch, status: "ACTIVE" }),
    enabled: Boolean(activeWorkspaceId && open),
  });

  function close() {
    setSearch("");
    setSelected(new Set());
    setError(null);
    setProgress(null);
    onOpenChange(false);
  }

  async function handleSubmit() {
    if (!activeWorkspaceId || selected.size === 0) return;
    setError(null);
    setSubmitting(true);
    const leadIds = [...selected];
    setProgress({ processed: 0, total: leadIds.length });
    try {
      const result = await addLeadsToListWithProgress(activeWorkspaceId, listId, leadIds, {
        onProgress: (p) => setProgress(p),
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list-members", listId],
      });
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "leads"] });
      toast(
        result.failed === 0
          ? `Added ${result.succeeded} lead${result.succeeded === 1 ? "" : "s"} to ${listName}.`
          : `Added ${result.succeeded}, ${result.failed} could not be added.`,
        result.failed > 0 ? "error" : undefined,
      );
      close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const leads = searchQuery.data?.items ?? [];

  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!submitting) close();
      }}
      title={`Add leads to ${listName}`}
      align="center"
      className="max-w-2xl"
      footer={
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-slate-500" aria-live="polite">
            {submitting && progress
              ? `Adding ${progress.processed} of ${progress.total}…`
              : `${selected.size} selected`}
          </span>
          <div className="flex gap-2">
            <Button type="button" variant="outline" disabled={submitting} onClick={close}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={selected.size === 0 || submitting}
              onClick={handleSubmit}
            >
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              Add {selected.size > 0 ? selected.size : ""} lead{selected.size === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-4 p-4">
        {error ? <Alert>{error}</Alert> : null}
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
            aria-hidden="true"
          />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search active leads by name, email or company"
            className="pl-9"
            disabled={submitting}
            autoFocus
          />
        </div>

        {searchQuery.isLoading ? (
          <div className="flex h-32 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : leads.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">
            {debouncedSearch
              ? "No active leads match that search."
              : "Search for leads to add to this list."}
          </p>
        ) : (
          <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
            {leads.map((lead) => (
              <label
                key={lead.id}
                className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(lead.id)}
                  disabled={submitting}
                  onChange={(event) =>
                    setSelected((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(lead.id);
                      else next.delete(lead.id);
                      return next;
                    })
                  }
                  className="h-4 w-4 rounded border-slate-300 text-brand-600"
                />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium text-slate-900">
                    {fullName(lead.first_name, lead.last_name)}
                  </span>
                  <span className="block text-slate-500">
                    {lead.email}
                    {lead.company ? ` · ${lead.company}` : ""}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
