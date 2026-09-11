"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Search } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createLead, listLeadLists, listLeads } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

type LeadFormState = {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  title: string;
  list_id: string;
};

const emptyForm: LeadFormState = {
  email: "",
  first_name: "",
  last_name: "",
  company: "",
  title: "",
  list_id: "",
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
          <h1 className="text-3xl font-semibold tracking-normal text-slate-950">
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
        <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold tracking-normal text-slate-950">
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
            <Field id="lead-title" label="Title">
              <Input
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                disabled={createMutation.isPending}
              />
            </Field>
            <div className="space-y-1.5 md:col-span-2">
              <label htmlFor="lead-list" className="text-sm font-medium text-slate-700">
                List
              </label>
              <select
                id="lead-list"
                value={form.list_id}
                onChange={(event) => setForm({ ...form, list_id: event.target.value })}
                disabled={createMutation.isPending}
                className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
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

      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <div className="grid gap-3 md:grid-cols-[1fr_180px_220px]">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
              aria-hidden="true"
            />
            <Input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search email, name, or company"
              className="pl-9"
            />
          </div>
          <select
            aria-label="Lead status"
            value={status}
            onChange={(event) => updateParam("status", event.target.value)}
            className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
          >
            <option value="ACTIVE">Active</option>
            <option value="ARCHIVED">Archived</option>
            <option value="ALL">All</option>
          </select>
          <select
            aria-label="Lead list filter"
            value={listId ?? ""}
            onChange={(event) => updateParam("list_id", event.target.value)}
            className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
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

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
        {leadsQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : leadsQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(leadsQuery.error)}</Alert>
          </div>
        ) : leadsQuery.data?.items.length === 0 ? (
          <div className="p-8 text-center">
            <h2 className="text-lg font-semibold text-slate-950">No leads yet</h2>
            <p className="mt-2 text-sm text-slate-500">
              Add a lead to start building this workspace&apos;s contact database.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-normal text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Lead</th>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3">Company</th>
                    <th className="px-4 py-3">Lists</th>
                    <th className="px-4 py-3">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {leadsQuery.data?.items.map((lead) => (
                    <tr key={lead.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-medium text-slate-950">
                        <Link href={`/app/leads/${lead.id}`}>
                          {fullName(lead.first_name, lead.last_name)}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-slate-700">{lead.email}</td>
                      <td className="px-4 py-3 text-slate-700">
                        {lead.company ?? "No company"}
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
    </main>
  );
}
