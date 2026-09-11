"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createLeadList, listLeadLists } from "@/lib/leads-api";
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
  const mayManage = canManageContacts(activeWorkspace?.role_code);

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", { cursor }],
    queryFn: () =>
      listLeadLists(activeWorkspaceId!, { limit: PAGE_SIZE, cursor }),
    enabled: Boolean(activeWorkspaceId),
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
          <h1 className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
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
        <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold tracking-normal text-slate-950">
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

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
        {listsQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : listsQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(listsQuery.error)}</Alert>
          </div>
        ) : listsQuery.data?.items.length === 0 ? (
          <div className="p-8 text-center">
            <h2 className="text-lg font-semibold text-slate-950">No lists yet</h2>
            <p className="mt-2 text-sm text-slate-500">
              Create a list to organize leads for later campaign selection.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-normal text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Name</th>
                    <th className="px-4 py-3">Members</th>
                    <th className="px-4 py-3">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {listsQuery.data?.items.map((list) => (
                    <tr key={list.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-medium text-slate-950">
                        <Link href={`/app/leads/lists/${list.id}`}>
                          {list.name}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {list.member_count}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatDate(list.created_at)}
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
                disabled={!listsQuery.data?.next_cursor}
                onClick={() => goToCursor(listsQuery.data?.next_cursor ?? null)}
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
