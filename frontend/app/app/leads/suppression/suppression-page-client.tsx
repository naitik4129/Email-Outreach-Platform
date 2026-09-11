"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Search, Trash2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  createManualSuppression,
  listSuppressions,
  releaseManualSuppression,
} from "@/lib/suppression-api";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

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

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(value),
  );
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export function SuppressionPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  
  const [searchText, setSearchText] = useState(params.get("q") ?? "");
  const [formOpen, setFormOpen] = useState(false);
  const [emailToSuppress, setEmailToSuppress] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const debouncedSearch = useDebouncedValue(searchText, 350);
  const cursor = params.get("cursor");
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

  const suppressionQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "suppressions",
      { cursor, q: params.get("q") },
    ],
    queryFn: () =>
      listSuppressions(activeWorkspaceId!, {
        limit: PAGE_SIZE,
        cursor,
        q: params.get("q"),
      }),
    enabled: Boolean(activeWorkspaceId),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createManualSuppression(activeWorkspaceId!, { email: emailToSuppress }),
    onSuccess: () => {
      setEmailToSuppress("");
      setFormOpen(false);
      setFormError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "suppressions"],
      });
    },
    onError: (error) => setFormError(errorMessage(error)),
  });

  const releaseMutation = useMutation({
    mutationFn: (suppressionId: string) =>
      releaseManualSuppression(activeWorkspaceId!, suppressionId, {
        reason: "Released via UI",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "suppressions"],
      });
    },
    // Not explicitly handling per-row error here for brevity, could add toast
  });

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
            Suppression List
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            Contacts that will never receive outreach (unsubscribed, bounced, or manually suppressed).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {mayManage ? (
            <Button type="button" onClick={() => setFormOpen((open) => !open)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              Suppress Email
            </Button>
          ) : null}
        </div>
      </div>

      {formOpen && mayManage ? (
        <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold tracking-normal text-slate-950">
            Manually Suppress Email
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
            <Field id="suppress-email" label="Email Address" className="md:col-span-2">
              <Input
                value={emailToSuppress}
                onChange={(event) => setEmailToSuppress(event.target.value)}
                disabled={createMutation.isPending}
                required
                type="email"
                placeholder="email@example.com"
              />
            </Field>
            
            <div className="flex items-center gap-2 md:col-span-2">
              <Button type="submit" disabled={createMutation.isPending || !emailToSuppress}>
                {createMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                Suppress
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
        <div className="grid gap-3 md:grid-cols-[1fr]">
          <div className="relative max-w-sm">
            <Search
              className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
              aria-hidden="true"
            />
            <Input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search by email"
              className="pl-9"
            />
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white shadow-sm">
        {suppressionQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : suppressionQuery.error ? (
          <div className="p-5">
            <Alert>{errorMessage(suppressionQuery.error)}</Alert>
          </div>
        ) : suppressionQuery.data?.items.length === 0 ? (
          <div className="p-8 text-center">
            <h2 className="text-lg font-semibold text-slate-950">No suppressions</h2>
            <p className="mt-2 text-sm text-slate-500">
              There are no suppressed emails matching your criteria.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200 text-sm">
                <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-normal text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Email</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Reason</th>
                    <th className="px-4 py-3">First Observed</th>
                    <th className="px-4 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {suppressionQuery.data?.items.map((suppression) => (
                    <tr key={suppression.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3 font-medium text-slate-950">
                        {suppression.email}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                         <span
                          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                            suppression.status === "ACTIVE"
                              ? "bg-red-100 text-red-800"
                              : "bg-slate-100 text-slate-800"
                          }`}
                        >
                          {suppression.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {suppression.reason}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {formatDate(suppression.first_observed_at)}
                      </td>
                      <td className="px-4 py-3 text-slate-700">
                        {suppression.removable && mayManage && (
                          <Button 
                            variant="ghost" 
                            size="icon"
                            onClick={() => {
                              if (confirm("Are you sure you want to release this manual suppression?")) {
                                releaseMutation.mutate(suppression.id);
                              }
                            }}
                            disabled={releaseMutation.isPending}
                            title="Release Suppression"
                          >
                            <Trash2 className="h-4 w-4 text-red-500" />
                          </Button>
                        )}
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
                disabled={!suppressionQuery.data?.next_cursor}
                onClick={() => goToCursor(suppressionQuery.data?.next_cursor ?? null)}
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
