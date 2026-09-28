"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2, Plus, RotateCcw, Search, Users } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SelectionBar } from "@/components/ui/selection-bar";
import { useToast } from "@/components/ui/toast";
import { LeadProfileFields } from "@/components/leads/lead-profile-fields";
import { ApiError } from "@/lib/api-client";
import {
  emptyProfileValues,
  formatLocation,
  profilePayload,
  type LeadProfileFormValues,
} from "@/lib/lead-fields";
import { summarizeBulk } from "@/lib/bulk-summary";
import {
  bulkArchiveLeads,
  bulkUnarchiveLeads,
  createLead,
  listLeadLists,
  listLeads,
} from "@/lib/leads-api";
import { useSelection } from "@/lib/use-selection";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

type LeadFormState = {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  title: string;
  list_id: string;
  profile: LeadProfileFormValues;
};

const emptyForm: LeadFormState = {
  email: "",
  first_name: "",
  last_name: "",
  company: "",
  title: "",
  list_id: "",
  profile: emptyProfileValues,
};

function canManageContacts(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER" || role === "MEMBER";
}

function useDebouncedValue(value: string, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

function fullName(firstName: string | null, lastName: string | null) {
  return [firstName, lastName].filter(Boolean).join(" ") || "Unnamed lead";
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

export function LeadsPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [searchText, setSearchText] = useState(params.get("q") ?? "");
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<LeadFormState>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);

  const debouncedSearch = useDebouncedValue(searchText, 350);
  const cursor = params.get("cursor");
  const status = (params.get("status") as "ACTIVE" | "ARCHIVED" | "ALL" | null) ?? "ACTIVE";
  const listId = params.get("list_id");
  const mayManage = canManageContacts(activeWorkspace?.role_code);
  const { toast } = useToast();
  const selection = useSelection();
  const [confirmBulkArchive, setConfirmBulkArchive] = useState(false);

  useEffect(() => {
    const next = new URLSearchParams(params.toString());
    if (debouncedSearch) next.set("q", debouncedSearch);
    else next.delete("q");
    next.delete("cursor");
    const href = `${pathname}?${next.toString()}`;
    if ((params.get("q") ?? "") !== debouncedSearch) {
      router.replace(href.endsWith("?") ? pathname : href);
    }
  }, [debouncedSearch, params, pathname, router]);

  const leadsQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "leads",
      { cursor, q: params.get("q"), status, listId },
    ],
    queryFn: () =>
      listLeads(activeWorkspaceId!, {
        limit: PAGE_SIZE,
        cursor,
        q: params.get("q"),
        status,
        list_id: listId,
      }),
    enabled: Boolean(activeWorkspaceId),
  });

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", "picker"],
    queryFn: () => listLeadLists(activeWorkspaceId!, { limit: 100 }),
    enabled: Boolean(activeWorkspaceId),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createLead(activeWorkspaceId!, {
        email: form.email,
        first_name: form.first_name || null,
        last_name: form.last_name || null,
        company: form.company || null,
        title: form.title || null,
        ...profilePayload(form.profile),
        custom_fields: {},
        list_id: form.list_id || null,
      }),
    onSuccess: () => {
      setForm(emptyForm);
      setFormOpen(false);
      setFormError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
    },
    onError: (error) => setFormError(errorMessage(error)),
  });

  const leads = leadsQuery.data?.items ?? [];
  const activeSelected = leads.filter(
    (lead) => selection.selected.has(lead.id) && !lead.archived_at,
  );
  const archivedSelected = leads.filter(
    (lead) => selection.selected.has(lead.id) && lead.archived_at,
  );

  // A different list (filter, search, page) must never leave hidden rows selected.
  const paramsKey = params.toString();
  const { clear: clearSelection } = selection;
  useEffect(() => {
    clearSelection();
  }, [status, cursor, listId, paramsKey, clearSelection]);

  const bulkMutation = useMutation({
    mutationFn: ({ restore }: { restore: boolean }) => {
      const source = restore ? archivedSelected : activeSelected;
      const items = source.map((lead) => ({
        id: lead.id,
        expected_version: lead.version,
      }));
      return restore
        ? bulkUnarchiveLeads(activeWorkspaceId!, items)
        : bulkArchiveLeads(activeWorkspaceId!, items);
    },
    onSuccess: (result, { restore }) => {
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "leads"] });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
      toast(
        summarizeBulk(result, restore ? "Restored" : "Archived"),
        result.failed > 0 ? "error" : undefined,
      );
      selection.clear();
      setConfirmBulkArchive(false);
    },
    onError: (error) => {
      setConfirmBulkArchive(false);
      toast(errorMessage(error), "error");
    },
  });

  function updateParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("cursor");
    const query = next.toString();
    router.push(query ? `${pathname}?${query}` : pathname);
  }

  function goToCursor(nextCursor: string | null) {
    const next = new URLSearchParams(params.toString());
    if (nextCursor) next.set("cursor", nextCursor);
    else next.delete("cursor");
    router.push(`${pathname}?${next.toString()}`);
  }

  return (
    <main className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Leads
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Workspace contacts for outreach and reusable lists.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button asChild variant="ghost">
            <Link href="/app/leads/lists">Lists</Link>
          </Button>
          {mayManage ? (
            <Button type="button" onClick={() => setFormOpen((open) => !open)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Lead
            </Button>
          ) : null}
        </div>
      </div>

      {formOpen && mayManage ? (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="text-lg font-semibold tracking-normal text-slate-900">
            Add lead
          </h2>
          {formError ? (
            <div className="mt-3">
              <Alert>{formError}</Alert>
            </div>
          ) : null}
          <form
            className="mt-4 grid gap-4 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              setFormError(null);
              createMutation.mutate();
            }}
          >
            <Field id="lead-email" label="Email" className="md:col-span-2">
              <Input
                value={form.email}
                onChange={(event) => setForm({ ...form, email: event.target.value })}
                disabled={createMutation.isPending}
                required
                type="email"
              />
            </Field>
            <Field id="lead-first-name" label="First name">
              <Input
                value={form.first_name}
                onChange={(event) =>
                  setForm({ ...form, first_name: event.target.value })
                }
                disabled={createMutation.isPending}
              />
            </Field>
            <Field id="lead-last-name" label="Last name">
              <Input
                value={form.last_name}
                onChange={(event) =>
                  setForm({ ...form, last_name: event.target.value })
                }
                disabled={createMutation.isPending}
              />
            </Field>
            <Field id="lead-company" label="Company">
              <Input
                value={form.company}
                onChange={(event) => setForm({ ...form, company: event.target.value })}
                disabled={createMutation.isPending}
              />
            </Field>
            <Field id="lead-title" label="Job title">
              <Input
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                disabled={createMutation.isPending}
              />
            </Field>
            <LeadProfileFields
              idPrefix="lead"
              values={form.profile}
              onChange={(key, value) =>
                setForm((current) => ({
                  ...current,
                  profile: { ...current.profile, [key]: value },
                }))
              }
              disabled={createMutation.isPending}
            />
            <div className="space-y-1.5 md:col-span-2">
              <label htmlFor="lead-list" className="text-sm font-medium text-slate-700">
                List
              </label>
              <select
                id="lead-list"
                value={form.list_id}
                onChange={(event) => setForm({ ...form, list_id: event.target.value })}
                disabled={createMutation.isPending}
                className="w-full rounded-md border border-slate-300 bg-white text-sm text-slate-900 shadow-sm transition-colors placeholder:text-slate-400 hover:border-slate-400 focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 disabled:opacity-70 aria-[invalid=true]:border-red-400 aria-[invalid=true]:focus-visible:ring-red-500/30 h-10 px-3"
              >
                <option value="">No list</option>
                {(listsQuery.data?.items ?? []).map((list) => (
                  <option key={list.id} value={list.id}>
                    {list.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 md:col-span-2">
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                Create
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={createMutation.isPending}
                onClick={() => setFormOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </form>
        </section>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="grid gap-3 md:grid-cols-[1fr_180px_220px]">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
              aria-hidden="true"
            />
            <Input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search name, email, company, title, or location"
              className="pl-9"
            />
          </div>
          <select
            aria-label="Lead status"
            value={status}
            onChange={(event) => updateParam("status", event.target.value)}
            className="w-full rounded-md border border-slate-300 bg-white text-sm text-slate-900 shadow-sm transition-colors placeholder:text-slate-400 hover:border-slate-400 focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 disabled:opacity-70 aria-[invalid=true]:border-red-400 aria-[invalid=true]:focus-visible:ring-red-500/30 h-10 px-3"
          >
            <option value="ACTIVE">Active</option>
            <option value="ARCHIVED">Archived</option>
            <option value="ALL">All</option>
          </select>
          <select
            aria-label="Lead list filter"
            value={listId ?? ""}
            onChange={(event) => updateParam("list_id", event.target.value)}
            className="w-full rounded-md border border-slate-300 bg-white text-sm text-slate-900 shadow-sm transition-colors placeholder:text-slate-400 hover:border-slate-400 focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 disabled:opacity-70 aria-[invalid=true]:border-red-400 aria-[invalid=true]:focus-visible:ring-red-500/30 h-10 px-3"
          >
            <option value="">All lists</option>
            {(listsQuery.data?.items ?? []).map((list) => (
              <option key={list.id} value={list.id}>
                {list.name}
              </option>
            ))}
          </select>
        </div>
      </section>

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
        {leadsQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : leadsQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(leadsQuery.error)}</Alert>
          </div>
        ) : leadsQuery.data?.items.length === 0 ? (
          <EmptyState
            className="rounded-none border-0"
            icon={<Users />}
            title="No leads yet"
            description="Add a lead to start building this workspace's contact database, or import a CSV to bring in many at once."
            action={
              mayManage ? (
                <>
                  <Button onClick={() => setFormOpen(true)}>
                    <Plus className="h-4 w-4" aria-hidden="true" />
                    Add lead
                  </Button>
                  <Button variant="outline" asChild>
                    <Link href="/app/leads/imports/new">Import from CSV</Link>
                  </Button>
                </>
              ) : null
            }
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
                          aria-label="Select all leads on this page"
                          checked={leads.length > 0 && selection.count === leads.length}
                          onChange={(event) =>
                            event.target.checked
                              ? selection.setAll(leads.map((lead) => lead.id))
                              : selection.clear()
                          }
                          className="h-4 w-4 rounded border-slate-300 text-brand-600"
                        />
                      </th>
                    ) : null}
                    <th className="px-4 py-3">Lead</th>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3">Company</th>
                    <th className="px-4 py-3">Job title</th>
                    <th className="px-4 py-3">Location</th>
                    <th className="px-4 py-3">Lists</th>
                    <th className="px-4 py-3">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {leadsQuery.data?.items.map((lead) => (
                    <tr key={lead.id} className="hover:bg-slate-50">
                      {mayManage ? (
                        <td className="w-10 px-4 py-3">
                          <input
                            type="checkbox"
                            aria-label={`Select ${lead.email}`}
                            checked={selection.selected.has(lead.id)}
                            onChange={() => selection.toggle(lead.id)}
                            className="h-4 w-4 rounded border-slate-300 text-brand-600"
                          />
                        </td>
                      ) : null}
                      <td className="px-4 py-3 font-medium text-slate-900">
                        <Link href={`/app/leads/${lead.id}`}>
                          {lead.erased_at
                            ? "Erased lead"
                            : fullName(lead.first_name, lead.last_name)}
                        </Link>
                        {lead.archived_at ? (
                          <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                            {lead.erased_at ? "Erased" : "Archived"}
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-slate-700">{lead.email}</td>
                      <td className="px-4 py-3 text-slate-700">
                        {lead.company ?? "No company"}
                      </td>
                      <td className="px-4 py-3 text-slate-700">{lead.title ?? "—"}</td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatLocation(lead) || "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-700">{lead.list_count}</td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatDate(lead.created_at)}
                      </td>
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
                disabled={!leadsQuery.data?.next_cursor}
                onClick={() => goToCursor(leadsQuery.data?.next_cursor ?? null)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </section>
      <ConfirmDialog
        open={confirmBulkArchive}
        title={`Archive ${activeSelected.length} lead${activeSelected.length === 1 ? "" : "s"}?`}
        description="Archived leads are left out of new campaigns and lists. You can restore them later. Their history is kept."
        confirmLabel="Archive"
        tone="danger"
        loading={bulkMutation.isPending}
        onCancel={() => setConfirmBulkArchive(false)}
        onConfirm={() => bulkMutation.mutate({ restore: false })}
      />
    </main>
  );
}
