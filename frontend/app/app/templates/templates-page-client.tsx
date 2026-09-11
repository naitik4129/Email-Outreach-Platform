"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Copy,
  FileText,
  Loader2,
  Plus,
  Search,
  Trash2,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  archiveTemplate,
  duplicateTemplate,
  listTemplates,
} from "@/lib/templates-api";
import { useWorkspace } from "@/lib/workspace-context";

const PAGE_SIZE = 25;

function canManageTemplates(role?: string) {
  return (
    role === "OWNER" ||
    role === "ADMIN" ||
    role === "MANAGER" ||
    role === "MEMBER"
  );
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
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export function TemplatesPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [searchText, setSearchText] = useState(params.get("q") ?? "");
  const [actionError, setActionError] = useState<string | null>(null);

  const debouncedSearch = useDebouncedValue(searchText, 350);
  const cursor = params.get("cursor");
  const status =
    (params.get("status") as "ACTIVE" | "ARCHIVED" | "ALL" | null) ?? "ACTIVE";
  const mayManage = canManageTemplates(activeWorkspace?.role_code);

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

  const templatesQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "templates",
      { cursor, q: params.get("q"), status },
    ],
    queryFn: () =>
      activeWorkspaceId
        ? listTemplates(activeWorkspaceId, {
            limit: PAGE_SIZE,
            cursor,
            q: params.get("q"),
            status,
          })
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const duplicateMutation = useMutation({
    mutationFn: (templateId: string) => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return duplicateTemplate(activeWorkspaceId, templateId);
    },
    onSuccess: (newTmpl) => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
      router.push(`/app/templates/${newTmpl.id}`);
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const archiveMutation = useMutation({
    mutationFn: ({
      templateId,
      version,
    }: {
      templateId: string;
      version: number;
    }) => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return archiveTemplate(activeWorkspaceId, templateId, {
        expected_version: version,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const templates = templatesQuery.data?.items ?? [];
  const nextCursor = templatesQuery.data?.next_cursor ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            Templates
          </h1>
          <p className="text-sm text-slate-500">
            Create and manage reusable email templates with deterministic variables.
          </p>
        </div>
        {mayManage && (
          <Button asChild>
            <Link href="/app/templates/new">
              <Plus className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Create Template
            </Link>
          </Button>
        )}
      </div>

      {actionError && (
        <Alert variant="error">
          <div className="flex w-full items-center justify-between">
            <span>{actionError}</span>
            <button
              type="button"
              className="text-xs underline hover:text-red-900"
              onClick={() => setActionError(null)}
            >
              Dismiss
            </button>
          </div>
        </Alert>
      )}

      {/* Filters and search */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <Input
            placeholder="Search by name or subject..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-slate-200 bg-slate-50 p-0.5 text-xs font-medium text-slate-600">
            <button
              type="button"
              onClick={() => {
                const next = new URLSearchParams(params.toString());
                next.set("status", "ACTIVE");
                next.delete("cursor");
                router.push(`${pathname}?${next.toString()}`);
              }}
              className={`rounded px-2.5 py-1 ${
                status === "ACTIVE"
                  ? "bg-white font-semibold text-slate-900 shadow-sm"
                  : "hover:text-slate-900"
              }`}
            >
              Active
            </button>
            <button
              type="button"
              onClick={() => {
                const next = new URLSearchParams(params.toString());
                next.set("status", "ARCHIVED");
                next.delete("cursor");
                router.push(`${pathname}?${next.toString()}`);
              }}
              className={`rounded px-2.5 py-1 ${
                status === "ARCHIVED"
                  ? "bg-white font-semibold text-slate-900 shadow-sm"
                  : "hover:text-slate-900"
              }`}
            >
              Archived
            </button>
            <button
              type="button"
              onClick={() => {
                const next = new URLSearchParams(params.toString());
                next.set("status", "ALL");
                next.delete("cursor");
                router.push(`${pathname}?${next.toString()}`);
              }}
              className={`rounded px-2.5 py-1 ${
                status === "ALL"
                  ? "bg-white font-semibold text-slate-900 shadow-sm"
                  : "hover:text-slate-900"
              }`}
            >
              All
            </button>
          </div>
        </div>
      </div>

      {/* Content states */}
      {templatesQuery.isLoading ? (
        <div className="flex min-h-[250px] items-center justify-center rounded-lg border border-slate-200 bg-white">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : templatesQuery.isError ? (
        <Alert variant="error">
          {errorMessage(templatesQuery.error)}
        </Alert>
      ) : templates.length === 0 ? (
        <div className="flex min-h-[300px] flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
            <FileText className="h-6 w-6" />
          </div>
          <h2 className="mt-3 text-base font-semibold text-slate-900">
            No templates yet
          </h2>
          <p className="mt-1 text-sm text-slate-500 max-w-sm">
            {debouncedSearch
              ? "No templates match your search query."
              : "Create reusable email templates to power your campaign outreach."}
          </p>
          {mayManage && !debouncedSearch && (
            <div className="mt-5">
              <Button asChild>
                <Link href="/app/templates/new">
                  <Plus className="mr-1.5 h-4 w-4" />
                  Create Template
                </Link>
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-6 py-3">Template</th>
                <th className="px-6 py-3">Subject</th>
                <th className="px-6 py-3">Revision</th>
                <th className="px-6 py-3">Updated</th>
                <th className="px-6 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {templates.map((tmpl) => (
                <tr key={tmpl.id} className="hover:bg-slate-50/50">
                  <td className="px-6 py-4 font-medium text-slate-900">
                    <Link
                      href={`/app/templates/${tmpl.id}`}
                      className="text-indigo-600 hover:text-indigo-800 hover:underline"
                    >
                      {tmpl.name}
                    </Link>
                    {tmpl.archived_at && (
                      <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                        Archived
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-4 text-slate-600 max-w-xs truncate">
                    {tmpl.subject || <span className="italic text-slate-400">No subject</span>}
                  </td>
                  <td className="px-6 py-4 text-slate-500">
                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
                      v{tmpl.current_revision ?? 1}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-slate-500 whitespace-nowrap">
                    {formatDate(tmpl.updated_at)}
                  </td>
                  <td className="px-6 py-4 text-right whitespace-nowrap">
                    <div className="flex items-center justify-end gap-1.5">
                      <Button variant="ghost" size="sm" asChild>
                        <Link href={`/app/templates/${tmpl.id}`}>
                          {mayManage ? "Edit" : "View"}
                        </Link>
                      </Button>
                      {mayManage && !tmpl.archived_at && (
                        <>
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Duplicate"
                            onClick={() => duplicateMutation.mutate(tmpl.id)}
                            disabled={duplicateMutation.isPending}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-600 hover:text-red-700"
                            title="Archive"
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Are you sure you want to archive "${tmpl.name}"?`
                                )
                              ) {
                                archiveMutation.mutate({
                                  templateId: tmpl.id,
                                  version: tmpl.version,
                                });
                              }
                            }}
                            disabled={archiveMutation.isPending}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-6 py-3">
            <div className="text-xs text-slate-500">
              Showing {templates.length} template{templates.length === 1 ? "" : "s"}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!cursor}
                onClick={() => {
                  const next = new URLSearchParams(params.toString());
                  next.delete("cursor");
                  router.push(`${pathname}?${next.toString()}`);
                }}
              >
                First page
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!nextCursor}
                onClick={() => {
                  if (nextCursor) {
                    const next = new URLSearchParams(params.toString());
                    next.set("cursor", nextCursor);
                    router.push(`${pathname}?${next.toString()}`);
                  }
                }}
              >
                Next
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
