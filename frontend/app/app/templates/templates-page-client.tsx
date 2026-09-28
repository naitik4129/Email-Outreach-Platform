"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Copy,
  FileText,
  Plus,
  RotateCcw,
  Search,
  Trash2,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { SelectionBar } from "@/components/ui/selection-bar";
import { useToast } from "@/components/ui/toast";
import { TypeToConfirmDialog } from "@/components/ui/type-to-confirm-dialog";
import { ApiError } from "@/lib/api-client";
import { summarizeBulk } from "@/lib/bulk-summary";
import { purgeTemplate } from "@/lib/erasure-api";
import { canEraseData } from "@/lib/permissions";
import {
  archiveTemplate,
  bulkArchiveTemplates,
  bulkUnarchiveTemplates,
  duplicateTemplate,
  listTemplates,
  unarchiveTemplate,
} from "@/lib/templates-api";
import { useSelection } from "@/lib/use-selection";
import { useWorkspace } from "@/lib/workspace-context";
import { LoadingBlock } from "@/components/ui/skeleton";

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
  const mayErase = canEraseData(activeWorkspace?.role_code);
  const { toast } = useToast();
  const selection = useSelection();

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

  const [archiveTarget, setArchiveTarget] = useState<{
    templateId: string;
    version: number;
    name: string;
  } | null>(null);
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

  const invalidateTemplates = () =>
    queryClient.invalidateQueries({
      queryKey: ["workspace", activeWorkspaceId, "templates"],
    });

  // A different list (filter, search, page) must never leave hidden rows selected.
  const paramsKey = params.toString();
  const { clear: clearSelection } = selection;
  useEffect(() => {
    clearSelection();
  }, [status, cursor, paramsKey, clearSelection]);

  const selectedTemplates = templates.filter((t) => selection.selected.has(t.id));
  const activeSelected = selectedTemplates.filter((t) => !t.archived_at);
  const archivedSelected = selectedTemplates.filter((t) => t.archived_at);
  const [confirmBulkArchive, setConfirmBulkArchive] = useState(false);

  const bulkArchive = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return bulkArchiveTemplates(
        activeWorkspaceId,
        activeSelected.map((t) => ({ id: t.id, expected_version: t.version })),
      );
    },
    onSuccess: (result) => {
      invalidateTemplates();
      toast(summarizeBulk(result, "Archived"), result.failed > 0 ? "error" : undefined);
      selection.clear();
      setConfirmBulkArchive(false);
    },
    onError: (err) => {
      setConfirmBulkArchive(false);
      setActionError(errorMessage(err));
    },
  });

  const bulkRestore = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return bulkUnarchiveTemplates(
        activeWorkspaceId,
        archivedSelected.map((t) => ({ id: t.id, expected_version: t.version })),
      );
    },
    onSuccess: (result) => {
      invalidateTemplates();
      toast(summarizeBulk(result, "Restored"), result.failed > 0 ? "error" : undefined);
      selection.clear();
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const restoreMutation = useMutation({
    mutationFn: ({ templateId, version }: { templateId: string; version: number }) => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return unarchiveTemplate(activeWorkspaceId, templateId, { expected_version: version });
    },
    onSuccess: () => {
      invalidateTemplates();
      toast("Template restored.");
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const [purgeTarget, setPurgeTarget] = useState<{ id: string; name: string } | null>(null);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const purgeMutation = useMutation({
    mutationFn: (target: { id: string; name: string }) => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return purgeTemplate(activeWorkspaceId, target.id, target.name);
    },
    onSuccess: () => {
      invalidateTemplates();
      setPurgeTarget(null);
      toast("Template deleted.");
    },
    onError: (err) => setPurgeError(errorMessage(err)),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
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
              loading={bulkRestore.isPending}
              onClick={() => bulkRestore.mutate()}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Restore selected
            </Button>
          ) : null}
        </SelectionBar>
      ) : null}

      {/* Content states */}
      {templatesQuery.isLoading ? (
        <LoadingBlock size="lg" />
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
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-left text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                {mayManage && (
                  <th className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      aria-label="Select all templates on this page"
                      checked={templates.length > 0 && selection.count === templates.length}
                      onChange={(event) =>
                        event.target.checked
                          ? selection.setAll(templates.map((t) => t.id))
                          : selection.clear()
                      }
                      className="h-4 w-4 rounded border-slate-300 text-brand-600"
                    />
                  </th>
                )}
                <th className="px-4 py-3">Template</th>
                <th className="hidden px-4 py-3 sm:table-cell">Subject</th>
                <th className="hidden px-4 py-3 md:table-cell">Revision</th>
                <th className="hidden px-4 py-3 md:table-cell">Updated</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {templates.map((tmpl) => (
                <tr key={tmpl.id} className="hover:bg-slate-50/50">
                  {mayManage && (
                    <td className="w-10 px-4 py-3.5">
                      <input
                        type="checkbox"
                        aria-label={`Select ${tmpl.name}`}
                        checked={selection.selected.has(tmpl.id)}
                        onChange={() => selection.toggle(tmpl.id)}
                        className="h-4 w-4 rounded border-slate-300 text-brand-600"
                      />
                    </td>
                  )}
                  <td className="px-4 py-3.5 font-medium text-slate-900">
                    <Link
                      href={`/app/templates/${tmpl.id}`}
                      className="text-brand-600 hover:text-brand-800 hover:underline"
                    >
                      {tmpl.name}
                    </Link>
                    {tmpl.archived_at && (
                      <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                        Archived
                      </span>
                    )}
                  </td>
                  <td className="hidden max-w-xs truncate px-4 py-3.5 text-slate-600 sm:table-cell">
                    {tmpl.subject || <span className="italic text-slate-400">No subject</span>}
                  </td>
                  <td className="hidden px-4 py-3.5 text-slate-500 md:table-cell">
                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
                      v{tmpl.current_revision ?? 1}
                    </span>
                  </td>
                  <td className="hidden whitespace-nowrap px-4 py-3.5 text-slate-500 md:table-cell">
                    {formatDate(tmpl.updated_at)}
                  </td>
                  <td className="px-4 py-3.5 text-right whitespace-nowrap">
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
                            onClick={() =>
                              setArchiveTarget({
                                templateId: tmpl.id,
                                version: tmpl.version,
                                name: tmpl.name,
                              })
                            }
                            disabled={archiveMutation.isPending}
                          >
                            <Archive className="h-3.5 w-3.5" />
                          </Button>
                        </>
                      )}
                      {mayManage && tmpl.archived_at && (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Restore"
                          onClick={() =>
                            restoreMutation.mutate({
                              templateId: tmpl.id,
                              version: tmpl.version,
                            })
                          }
                          disabled={restoreMutation.isPending}
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                          <span className="ml-1">Restore</span>
                        </Button>
                      )}
                      {mayErase && tmpl.archived_at && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-red-600 hover:text-red-700"
                          title="Delete permanently"
                          onClick={() => {
                            setPurgeError(null);
                            setPurgeTarget({ id: tmpl.id, name: tmpl.name });
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          <span className="sr-only">Delete permanently</span>
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>

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
      <ConfirmDialog
        open={archiveTarget !== null}
        title={`Archive "${archiveTarget?.name ?? ""}"?`}
        description="Archived templates can no longer be used in new sequences."
        confirmLabel="Archive template"
        tone="danger"
        loading={archiveMutation.isPending}
        onCancel={() => setArchiveTarget(null)}
        onConfirm={() => {
          if (archiveTarget) {
            archiveMutation.mutate(
              { templateId: archiveTarget.templateId, version: archiveTarget.version },
              { onSettled: () => setArchiveTarget(null) },
            );
          }
        }}
      />
      <ConfirmDialog
        open={confirmBulkArchive}
        title={`Archive ${activeSelected.length} template${activeSelected.length === 1 ? "" : "s"}?`}
        description="Archived templates can no longer be used in new sequences. You can restore them later."
        confirmLabel="Archive"
        tone="danger"
        loading={bulkArchive.isPending}
        onCancel={() => setConfirmBulkArchive(false)}
        onConfirm={() => bulkArchive.mutate()}
      />
      <TypeToConfirmDialog
        open={purgeTarget !== null}
        title={`Delete "${purgeTarget?.name ?? ""}" permanently?`}
        description="The template and all its versions are deleted for good. A template that was used in a campaign step can't be deleted and stays archived. This can't be undone."
        phrase={purgeTarget?.name ?? ""}
        confirmLabel="Delete permanently"
        loading={purgeMutation.isPending}
        error={purgeError}
        onCancel={() => setPurgeTarget(null)}
        onConfirm={() => purgeTarget && purgeMutation.mutate(purgeTarget)}
      />
    </div>
  );
}
