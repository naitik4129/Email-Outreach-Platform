"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ListChecks, Loader2, Plus, RotateCcw, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SelectionBar } from "@/components/ui/selection-bar";
import { useToast } from "@/components/ui/toast";
import { TypeToConfirmDialog } from "@/components/ui/type-to-confirm-dialog";
import { ApiError } from "@/lib/api-client";
import { summarizeBulk } from "@/lib/bulk-summary";
import { purgeLeadList } from "@/lib/erasure-api";
import {
  bulkArchiveLeadLists,
  bulkUnarchiveLeadLists,
  createLeadList,
  listLeadLists,
  unarchiveLeadList,
} from "@/lib/leads-api";
import { canEraseData } from "@/lib/permissions";
import { useSelection } from "@/lib/use-selection";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

function canManageContacts(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER" || role === "MEMBER";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(value),
  );
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export function LeadListsPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const cursor = params.get("cursor");
  const status = (params.get("status") as "ACTIVE" | "ARCHIVED" | "ALL" | null) ?? "ACTIVE";
  const mayManage = canManageContacts(activeWorkspace?.role_code);
  const mayErase = canEraseData(activeWorkspace?.role_code);
  const { toast } = useToast();
  const selection = useSelection();
  const [confirmBulkArchive, setConfirmBulkArchive] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<{ id: string; name: string } | null>(null);
  const [purgeError, setPurgeError] = useState<string | null>(null);

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", { cursor, status }],
    queryFn: () =>
      listLeadLists(activeWorkspaceId!, { limit: PAGE_SIZE, cursor, status }),
    enabled: Boolean(activeWorkspaceId),
  });

  const lists = listsQuery.data?.items ?? [];
  const activeSelected = lists.filter((l) => selection.selected.has(l.id) && !l.archived_at);
  const archivedSelected = lists.filter((l) => selection.selected.has(l.id) && l.archived_at);

  // A different list (filter, page) must never leave hidden rows selected.
  const paramsKey = params.toString();
  const { clear: clearSelection } = selection;
  useEffect(() => {
    clearSelection();
  }, [status, cursor, paramsKey, clearSelection]);

  const invalidateLists = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
    });

  const bulkMutation = useMutation({
    mutationFn: ({ restore }: { restore: boolean }) => {
      const source = restore ? archivedSelected : activeSelected;
      const items = source.map((l) => ({ id: l.id, expected_version: l.version }));
      return restore
        ? bulkUnarchiveLeadLists(activeWorkspaceId!, items)
        : bulkArchiveLeadLists(activeWorkspaceId!, items);
    },
    onSuccess: (result, { restore }) => {
      invalidateLists();
      toast(
        summarizeBulk(result, restore ? "Restored" : "Archived"),
        result.failed > 0 ? "error" : undefined,
      );
      selection.clear();
      setConfirmBulkArchive(false);
    },
    onError: (error) => {
      setConfirmBulkArchive(false);
      setActionError(errorMessage(error));
    },
  });

  const restoreMutation = useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      unarchiveLeadList(activeWorkspaceId!, id, version),
    onSuccess: () => {
      invalidateLists();
      toast("List restored.");
    },
    onError: (error) => setActionError(errorMessage(error)),
  });

  const purgeMutation = useMutation({
    mutationFn: (target: { id: string; name: string }) =>
      purgeLeadList(activeWorkspaceId!, target.id, target.name),
    onSuccess: () => {
      invalidateLists();
      setPurgeTarget(null);
      toast("List deleted. The leads in it were not deleted.");
    },
    onError: (error) => setPurgeError(errorMessage(error)),
  });

  const createMutation = useMutation({
    mutationFn: () => createLeadList(activeWorkspaceId!, name),
    onSuccess: () => {
      setName("");
      setFormOpen(false);
      setFormError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
    },
    onError: (error) => setFormError(errorMessage(error)),
  });

  function updateStatus(value: string) {
    const next = new URLSearchParams(params.toString());
    if (value && value !== "ACTIVE") next.set("status", value);
    else next.delete("status");
    next.delete("cursor");
    const query = next.toString();
    router.push(query ? `${pathname}?${query}` : pathname);
  }

  function goToCursor(nextCursor: string | null) {
    const next = new URLSearchParams(params.toString());
    if (nextCursor) next.set("cursor", nextCursor);
    else next.delete("cursor");
    const query = next.toString();
    router.push(query ? `${pathname}?${query}` : pathname);
  }

  return (
    <main className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <Button asChild variant="ghost">
            <Link href="/app/leads">Back to leads</Link>
          </Button>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 mt-3">
            Lead Lists
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Reusable collections of workspace leads.
          </p>
        </div>
        {mayManage ? (
          <Button type="button" onClick={() => setFormOpen((open) => !open)}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Create List
          </Button>
        ) : null}
      </div>

      {formOpen && mayManage ? (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="text-lg font-semibold tracking-normal text-slate-900">
            Create list
          </h2>
          {formError ? (
            <div className="mt-3">
              <Alert>{formError}</Alert>
            </div>
          ) : null}
          <form
            className="mt-4 flex max-w-lg items-start gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setFormError(null);
              createMutation.mutate();
            }}
          >
            <Field id="list-name" label="Name" className="flex-1">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={createMutation.isPending}
                required
              />
            </Field>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              Create
            </Button>
          </form>
        </section>
      ) : null}

      {actionError ? <Alert>{actionError}</Alert> : null}

      <div className="flex items-center gap-2">
        <select
          aria-label="List status"
          value={status}
          onChange={(event) => updateStatus(event.target.value)}
          className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-900 shadow-sm focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
        >
          <option value="ACTIVE">Active</option>
          <option value="ARCHIVED">Archived</option>
          <option value="ALL">All</option>
        </select>
      </div>

      {mayManage ? (
        <SelectionBar count={selection.count} onClear={selection.clear}>
          {activeSelected.length > 0 ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setConfirmBulkArchive(true)}
            >
              <Archive className="h-4 w-4" aria-hidden="true" />
              Archive selected
            </Button>
          ) : null}
          {archivedSelected.length > 0 ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={bulkMutation.isPending}
              onClick={() => bulkMutation.mutate({ restore: true })}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Restore selected
            </Button>
          ) : null}
        </SelectionBar>
      ) : null}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
        {listsQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : listsQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(listsQuery.error)}</Alert>
          </div>
        ) : listsQuery.data?.items.length === 0 ? (
          <EmptyState
            className="rounded-none border-0"
            icon={<ListChecks />}
            title="No lists yet"
            description="Create a list to organize leads for later campaign selection."
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <tr>
                    {mayManage ? (
                      <th className="w-10 px-4 py-3">
                        <input
                          type="checkbox"
                          aria-label="Select all lists on this page"
                          checked={lists.length > 0 && selection.count === lists.length}
                          onChange={(event) =>
                            event.target.checked
                              ? selection.setAll(lists.map((l) => l.id))
                              : selection.clear()
                          }
                          className="h-4 w-4 rounded border-slate-300 text-brand-600"
                        />
                      </th>
                    ) : null}
                    <th className="px-4 py-3">Name</th>
                    <th className="px-4 py-3">Members</th>
                    <th className="px-4 py-3">Created</th>
                    {mayManage ? <th className="px-4 py-3 text-right">Actions</th> : null}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {lists.map((list) => (
                    <tr key={list.id} className="hover:bg-slate-50">
                      {mayManage ? (
                        <td className="w-10 px-4 py-3">
                          <input
                            type="checkbox"
                            aria-label={`Select ${list.name}`}
                            checked={selection.selected.has(list.id)}
                            onChange={() => selection.toggle(list.id)}
                            className="h-4 w-4 rounded border-slate-300 text-brand-600"
                          />
                        </td>
                      ) : null}
                      <td className="px-4 py-3 font-medium text-slate-900">
                        <Link href={`/app/leads/lists/${list.id}`}>
                          {list.name}
                        </Link>
                        {list.archived_at ? (
                          <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                            Archived
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {list.member_count}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatDate(list.created_at)}
                      </td>
                      {mayManage ? (
                        <td className="whitespace-nowrap px-4 py-3 text-right">
                          {list.archived_at ? (
                            <div className="flex items-center justify-end gap-1.5">
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                disabled={restoreMutation.isPending}
                                onClick={() =>
                                  restoreMutation.mutate({ id: list.id, version: list.version })
                                }
                              >
                                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                                <span className="ml-1">Restore</span>
                              </Button>
                              {mayErase ? (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  className="text-red-600 hover:text-red-700"
                                  onClick={() => {
                                    setPurgeError(null);
                                    setPurgeTarget({ id: list.id, name: list.name });
                                  }}
                                >
                                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                                  <span className="sr-only">Delete {list.name} permanently</span>
                                </Button>
                              ) : null}
                            </div>
                          ) : null}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-slate-200 p-3">
              <Button
                type="button"
                variant="ghost"
                disabled={!cursor}
                onClick={() => goToCursor(null)}
              >
                First
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={!listsQuery.data?.next_cursor}
                onClick={() => goToCursor(listsQuery.data?.next_cursor ?? null)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </section>
      <ConfirmDialog
        open={confirmBulkArchive}
        title={`Archive ${activeSelected.length} list${activeSelected.length === 1 ? "" : "s"}?`}
        description="Archived lists can't be used for new campaigns. The leads in them are not affected. You can restore them later."
        confirmLabel="Archive"
        tone="danger"
        loading={bulkMutation.isPending}
        onCancel={() => setConfirmBulkArchive(false)}
        onConfirm={() => bulkMutation.mutate({ restore: false })}
      />
      <TypeToConfirmDialog
        open={purgeTarget !== null}
        title={`Delete "${purgeTarget?.name ?? ""}" permanently?`}
        description="The list is deleted for good. The leads in it are not deleted. A list that was used to build a campaign audience can't be deleted and stays archived. This can't be undone."
        phrase={purgeTarget?.name ?? ""}
        confirmLabel="Delete permanently"
        loading={purgeMutation.isPending}
        error={purgeError}
        onCancel={() => setPurgeTarget(null)}
        onConfirm={() => purgeTarget && purgeMutation.mutate(purgeTarget)}
      />
    </main>
  );
}
