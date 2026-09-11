"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2, Pencil, Plus, Search, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  addLeadListMember,
  archiveLeadList,
  getLeadList,
  listLeadListMembers,
  listLeads,
  removeLeadListMember,
  updateLeadList,
} from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

function canManageContacts(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER" || role === "MEMBER";
}

function fullName(firstName: string | null, lastName: string | null) {
  return [firstName, lastName].filter(Boolean).join(" ") || "Unnamed lead";
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function useDebouncedValue(value: string, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(handle);
  }, [value, delayMs]);
  return debounced;
}

export function LeadListDetailClient({ listId }: { listId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [memberSearch, setMemberSearch] = useState("");
  const [selectedLeadId, setSelectedLeadId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const cursor = params.get("cursor");
  const mayManage = canManageContacts(activeWorkspace?.role_code);
  const debouncedMemberSearch = useDebouncedValue(memberSearch, 350);

  const listQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-list", listId],
    queryFn: () => getLeadList(activeWorkspaceId!, listId),
    enabled: Boolean(activeWorkspaceId),
  });

  const membersQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-list-members", listId, { cursor }],
    queryFn: () =>
      listLeadListMembers(activeWorkspaceId!, listId, {
        limit: PAGE_SIZE,
        cursor,
      }),
    enabled: Boolean(activeWorkspaceId),
  });

  const searchQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "leads",
      "member-search",
      debouncedMemberSearch,
    ],
    queryFn: () =>
      listLeads(activeWorkspaceId!, {
        limit: 10,
        q: debouncedMemberSearch,
        status: "ACTIVE",
      }),
    enabled: Boolean(activeWorkspaceId && mayManage && debouncedMemberSearch),
  });

  useEffect(() => {
    if (listQuery.data && !editing) setName(listQuery.data.name);
  }, [listQuery.data, editing]);

  const renameMutation = useMutation({
    mutationFn: () =>
      updateLeadList(activeWorkspaceId!, listId, {
        name,
        expected_version: listQuery.data!.version,
      }),
    onSuccess: () => {
      setEditing(false);
      setError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const archiveMutation = useMutation({
    mutationFn: () =>
      archiveLeadList(activeWorkspaceId!, listId, listQuery.data!.version),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
      router.push("/app/leads/lists");
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const addMutation = useMutation({
    mutationFn: () => addLeadListMember(activeWorkspaceId!, listId, selectedLeadId),
    onSuccess: () => {
      setSelectedLeadId("");
      setMemberSearch("");
      setError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list-members", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const removeMutation = useMutation({
    mutationFn: (leadId: string) =>
      removeLeadListMember(activeWorkspaceId!, listId, leadId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-list-members", listId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  function goToCursor(nextCursor: string | null) {
    const next = new URLSearchParams(params.toString());
    if (nextCursor) next.set("cursor", nextCursor);
    else next.delete("cursor");
    const query = next.toString();
    router.push(query ? `${pathname}?${query}` : pathname);
  }

  if (listQuery.isLoading) {
    return (
      <main className="grid min-h-80 place-items-center">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (listQuery.error) {
    return (
      <main className="space-y-4">
        <Button asChild variant="ghost">
          <Link href="/app/leads/lists">Back to lists</Link>
        </Button>
        <Alert>{errorMessage(listQuery.error)}</Alert>
      </main>
    );
  }

  const list = listQuery.data;
  if (!list) return null;

  return (
    <main className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <Button asChild variant="ghost">
            <Link href="/app/leads/lists">Back to lists</Link>
          </Button>
          <h1 className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
            {list.name}
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            {list.member_count} members
          </p>
        </div>
        {mayManage && !list.archived_at ? (
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil className="h-4 w-4" aria-hidden="true" />
              Rename
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={archiveMutation.isPending}
              onClick={() => archiveMutation.mutate()}
            >
              <Archive className="h-4 w-4" aria-hidden="true" />
              Archive
            </Button>
          </div>
        ) : null}
      </div>

      {error ? <Alert>{error}</Alert> : null}

      {editing && mayManage ? (
        <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <form
            className="flex max-w-lg items-start gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              setError(null);
              renameMutation.mutate();
            }}
          >
            <Field id="rename-list" label="List name" className="flex-1">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={renameMutation.isPending}
                required
              />
            </Field>
            <Button type="submit" disabled={renameMutation.isPending}>
              {renameMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : null}
              Save
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </form>
        </section>
      ) : null}

      {mayManage && !list.archived_at ? (
        <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold tracking-normal text-slate-950">
            Add member
          </h2>
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
                aria-hidden="true"
              />
              <Input
                value={memberSearch}
                onChange={(event) => setMemberSearch(event.target.value)}
                placeholder="Search active leads"
                className="pl-9"
              />
            </div>
            <select
              aria-label="Lead to add"
              value={selectedLeadId}
              onChange={(event) => setSelectedLeadId(event.target.value)}
              className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
            >
              <option value="">Select lead</option>
              {(searchQuery.data?.items ?? []).map((lead) => (
                <option key={lead.id} value={lead.id}>
                  {fullName(lead.first_name, lead.last_name)} ({lead.email})
                </option>
              ))}
            </select>
            <Button
              type="button"
              disabled={!selectedLeadId || addMutation.isPending}
              onClick={() => addMutation.mutate()}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add
            </Button>
          </div>
        </section>
      ) : null}

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
        {membersQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : membersQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(membersQuery.error)}</Alert>
          </div>
        ) : membersQuery.data?.items.length === 0 ? (
          <div className="p-8 text-center">
            <h2 className="text-lg font-semibold text-slate-950">
              No leads in this list
            </h2>
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
                    <th className="px-4 py-3">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {membersQuery.data?.items.map((member) => (
                    <tr key={member.lead.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-medium text-slate-950">
                        <Link href={`/app/leads/${member.lead.id}`}>
                          {fullName(member.lead.first_name, member.lead.last_name)}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {member.lead.email}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {member.lead.company ?? "No company"}
                      </td>
                      <td className="px-4 py-3">
                        {mayManage && !list.archived_at ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            aria-label={`Remove ${member.lead.email}`}
                            disabled={removeMutation.isPending}
                            onClick={() => removeMutation.mutate(member.lead.id)}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </Button>
                        ) : null}
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
                disabled={!membersQuery.data?.next_cursor}
                onClick={() => goToCursor(membersQuery.data?.next_cursor ?? null)}
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
