"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Clock,
  Copy,
  Eye,
  FileText,
  History,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  archiveTemplate,
  duplicateTemplate,
  getTemplate,
  listTemplateVersions,
  previewTemplate,
  updateTemplate,
} from "@/lib/templates-api";
import { useWorkspace } from "@/lib/workspace-context";

function canManageTemplates(role?: string) {
  return (
    role === "OWNER" ||
    role === "ADMIN" ||
    role === "MANAGER" ||
    role === "MEMBER"
  );
}

const COMMON_VARIABLES = [
  { label: "First Name", code: "{{first_name}}" },
  { label: "First Name (with fallback)", code: "{{first_name|there}}" },
  { label: "Last Name", code: "{{last_name}}" },
  { label: "Company", code: "{{company}}" },
  { label: "Title", code: "{{title}}" },
  { label: "Email", code: "{{email}}" },
  { label: "Custom Field", code: "{{custom.industry}}" },
];

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function TemplateDetailPage() {
  const router = useRouter();
  const params = useParams<{ template_id: string }>();
  const templateId = params.template_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();

  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState("");
  const [activeField, setActiveField] = useState<"subject" | "body">("body");
  const [formError, setFormError] = useState<string | null>(null);
  const [concurrencyConflict, setConcurrencyConflict] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Preview state
  const [previewSubject, setPreviewSubject] = useState("");
  const [previewBody, setPreviewBody] = useState("");
  const [detectedVars, setDetectedVars] = useState<string[]>([]);
  const [missingVars, setMissingVars] = useState<string[]>([]);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const mayManage = canManageTemplates(activeWorkspace?.role_code);

  const templateQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "templates", templateId],
    queryFn: () =>
      activeWorkspaceId && templateId
        ? getTemplate(activeWorkspaceId, templateId)
        : Promise.reject(new Error("Missing context")),
    enabled: Boolean(activeWorkspaceId && templateId),
  });

  const versionsQuery = useQuery({
    queryKey: [
      "workspace",
      activeWorkspaceId,
      "templates",
      templateId,
      "versions",
    ],
    queryFn: () =>
      activeWorkspaceId && templateId
        ? listTemplateVersions(activeWorkspaceId, templateId)
        : Promise.reject(new Error("Missing context")),
    enabled: Boolean(activeWorkspaceId && templateId),
  });

  useEffect(() => {
    if (templateQuery.data) {
      setName(templateQuery.data.name);
      setSubject(templateQuery.data.subject);
      setBodyHtml(templateQuery.data.body_html);
      setConcurrencyConflict(false);
      setFormError(null);
    }
  }, [templateQuery.data]);

  // Live preview effect
  useEffect(() => {
    if (!activeWorkspaceId || !subject.trim()) {
      setPreviewSubject("");
      setPreviewBody("");
      return;
    }

    const timer = setTimeout(async () => {
      setIsPreviewLoading(true);
      setPreviewError(null);
      try {
        const res = await previewTemplate(activeWorkspaceId, {
          subject,
          body_html: bodyHtml,
        });
        setPreviewSubject(res.subject);
        setPreviewBody(res.body_html);
        setDetectedVars(res.detected_variables);
        setMissingVars(res.missing_variables);
      } catch (err) {
        if (err instanceof ApiError) {
          setPreviewError(err.message);
        } else {
          setPreviewError("Preview generation failed");
        }
      } finally {
        setIsPreviewLoading(false);
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [activeWorkspaceId, subject, bodyHtml]);

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !templateQuery.data) {
        throw new Error("Missing workspace or template");
      }
      return updateTemplate(activeWorkspaceId, templateId, {
        expected_version: templateQuery.data.version,
        name,
        subject,
        body_html: bodyHtml,
      });
    },
    onSuccess: (updated) => {
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
      setConcurrencyConflict(false);
      queryClient.setQueryData(
        ["workspace", activeWorkspaceId, "templates", templateId],
        updated
      );
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
      queryClient.invalidateQueries({
        queryKey: [
          "workspace",
          activeWorkspaceId,
          "templates",
          templateId,
          "versions",
        ],
      });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setConcurrencyConflict(true);
        setFormError(
          "This template was modified by another user. Reload the latest version to resolve this conflict."
        );
      } else if (err instanceof ApiError) {
        setFormError(err.message);
      } else {
        setFormError("Failed to update template.");
      }
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId) throw new Error("Missing workspace");
      return duplicateTemplate(activeWorkspaceId, templateId);
    },
    onSuccess: (dup) => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
      router.push(`/app/templates/${dup.id}`);
    },
    onError: (err) => {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to duplicate template."
      );
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !templateQuery.data) {
        throw new Error("Missing workspace or template");
      }
      return archiveTemplate(activeWorkspaceId, templateId, {
        expected_version: templateQuery.data.version,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
      router.push("/app/templates");
    },
    onError: (err) => {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to archive template."
      );
    },
  });

  function insertVariable(varCode: string) {
    if (activeField === "subject") {
      const input = subjectRef.current;
      if (!input) {
        setSubject((prev) => prev + varCode);
        return;
      }
      const start = input.selectionStart ?? input.value.length;
      const end = input.selectionEnd ?? input.value.length;
      const next = input.value.slice(0, start) + varCode + input.value.slice(end);
      setSubject(next);
      setTimeout(() => {
        input.focus();
        input.setSelectionRange(start + varCode.length, start + varCode.length);
      }, 0);
    } else {
      const textarea = bodyRef.current;
      if (!textarea) {
        setBodyHtml((prev) => prev + varCode);
        return;
      }
      const start = textarea.selectionStart ?? textarea.value.length;
      const end = textarea.selectionEnd ?? textarea.value.length;
      const next = textarea.value.slice(0, start) + varCode + textarea.value.slice(end);
      setBodyHtml(next);
      setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(start + varCode.length, start + varCode.length);
      }, 0);
    }
  }

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!name.trim()) {
      setFormError("Template name is required.");
      return;
    }
    if (!subject.trim()) {
      setFormError("Subject is required.");
      return;
    }
    updateMutation.mutate();
  }

  if (templateQuery.isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
      </div>
    );
  }

  if (templateQuery.isError || !templateQuery.data) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-12 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-500">
          <FileText className="h-6 w-6" />
        </div>
        <h1 className="text-lg font-semibold text-slate-900">
          Template Not Found
        </h1>
        <p className="text-sm text-slate-500">
          The requested template does not exist or you do not have permission to view it.
        </p>
        <div className="pt-2">
          <Button asChild variant="outline">
            <Link href="/app/templates">
              <ArrowLeft className="mr-1.5 h-4 w-4" />
              Back to templates
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  const tmpl = templateQuery.data;
  const isArchived = Boolean(tmpl.archived_at);

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-12">
      {/* Top action bar */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/app/templates">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-slate-900">
                {tmpl.name}
              </h1>
              <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
                Revision v{tmpl.current_revision ?? 1}
              </span>
              {isArchived && (
                <span className="inline-flex items-center rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                  Archived
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              Last modified {formatDate(tmpl.updated_at)} • Version sequence #{tmpl.version}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {mayManage && !isArchived && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => duplicateMutation.mutate()}
                disabled={duplicateMutation.isPending}
                title="Create a copy of this template"
              >
                <Copy className="mr-1.5 h-4 w-4" />
                Duplicate
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="text-red-600 hover:text-red-700"
                onClick={() => {
                  if (
                    window.confirm(
                      `Are you sure you want to archive "${tmpl.name}"?`
                    )
                  ) {
                    archiveMutation.mutate();
                  }
                }}
                disabled={archiveMutation.isPending}
              >
                <Trash2 className="mr-1.5 h-4 w-4" />
                Archive
              </Button>
              <Button
                onClick={handleSave}
                disabled={updateMutation.isPending}
              >
                {updateMutation.isPending ? (
                  <>
                    <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    Saving...
                  </>
                ) : (
                  "Save Changes"
                )}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Alerts */}
      {saveSuccess && (
        <Alert variant="success">
          Template updated and published as a new revision.
        </Alert>
      )}

      {concurrencyConflict && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-red-600 shrink-0 mt-0.5" />
            <div className="flex-1 text-sm text-red-800">
              <p className="font-semibold">Conflict: Stale Content Version</p>
              <p className="mt-1 text-xs">
                This template was edited by someone else while you had it open.
                To prevent accidental overwrites, please reload the latest version.
              </p>
              <div className="mt-3">
                <Button
                  size="sm"
                  variant="outline"
                  className="bg-white text-red-900 border-red-300 hover:bg-red-50"
                  onClick={() => templateQuery.refetch()}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Reload Latest Version
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {formError && !concurrencyConflict && (
        <Alert variant="error">{formError}</Alert>
      )}

      {!mayManage && (
        <div className="rounded-md bg-slate-100 p-3 text-xs text-slate-700">
          Viewing template in read-only mode (Viewer permission).
        </div>
      )}

      {/* Main Grid: Form + Preview */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Editor */}
        <div className="space-y-5 lg:col-span-7">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            <Field label="Template Name" required>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={!mayManage || isArchived}
                maxLength={200}
                required
              />
            </Field>

            <Field label="Subject Line" required>
              <Input
                ref={subjectRef}
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                onFocus={() => setActiveField("subject")}
                disabled={!mayManage || isArchived}
                maxLength={500}
                required
              />
            </Field>

            {/* Variable Pills Toolbar */}
            {mayManage && !isArchived && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-slate-600 flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5 text-indigo-600" />
                  Insert Variable (targets {activeField === "subject" ? "Subject" : "Body"})
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {COMMON_VARIABLES.map((v) => (
                    <button
                      key={v.label}
                      type="button"
                      onClick={() => insertVariable(v.code)}
                      className="inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs font-mono text-slate-700 hover:bg-indigo-50 hover:border-indigo-300 hover:text-indigo-700 transition"
                    >
                      + {v.code}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <Field label="Email Body (HTML/Text)" required>
              <textarea
                ref={bodyRef}
                rows={12}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:bg-slate-50"
                value={bodyHtml}
                onChange={(e) => setBodyHtml(e.target.value)}
                onFocus={() => setActiveField("body")}
                disabled={!mayManage || isArchived}
                maxLength={200000}
              />
            </Field>
          </div>

          {/* Version History Drawer/Section */}
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm space-y-3">
            <div className="flex items-center gap-2 border-b border-slate-100 pb-2">
              <History className="h-4 w-4 text-slate-500" />
              <h3 className="text-sm font-semibold text-slate-800">
                Immutable Revision History
              </h3>
            </div>
            {versionsQuery.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
            ) : (
              <div className="divide-y divide-slate-100 text-xs text-slate-600">
                {(versionsQuery.data ?? []).map((v) => (
                  <div
                    key={v.id}
                    className="py-2 flex items-center justify-between"
                  >
                    <div>
                      <span className="font-semibold text-slate-900">
                        Revision #{v.revision}
                      </span>
                      <span className="ml-2 text-slate-500 truncate max-w-xs inline-block align-middle">
                        &quot;{v.subject}&quot;
                      </span>
                    </div>
                    <div className="text-slate-400 whitespace-nowrap">
                      {formatDate(v.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Preview */}
        <div className="space-y-4 lg:col-span-5">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Eye className="h-4 w-4 text-indigo-600" />
                <h2 className="text-sm font-semibold text-slate-900">
                  Live Preview
                </h2>
              </div>
              {isPreviewLoading && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" />
              )}
            </div>

            {previewError && (
              <Alert variant="error">
                <span className="text-xs">{previewError}</span>
              </Alert>
            )}

            {missingVars.length > 0 && (
              <div className="rounded-md bg-amber-50 p-2.5 text-xs text-amber-800 border border-amber-200">
                <strong>Missing fallback:</strong> Variables without sample data:{" "}
                {missingVars.join(", ")}. Use <code className="font-mono">{"{{var|fallback}}"}</code> to provide default text.
              </div>
            )}

            {/* Rendered Subject */}
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Rendered Subject
              </label>
              <div className="mt-1 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-800 min-h-[38px]">
                {previewSubject || <span className="italic text-slate-400">Enter subject...</span>}
              </div>
            </div>

            {/* Rendered Body Sandbox */}
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Rendered Body (Isolated Sandbox)
              </label>
              <div className="mt-1 rounded-md border border-slate-200 bg-white overflow-hidden shadow-inner min-h-[220px]">
                <iframe
                  title="Template Detail Preview"
                  sandbox=""
                  srcDoc={
                    previewBody
                      ? `<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;font-size:14px;line-height:1.5;color:#1e293b;padding:16px;margin:0;word-break:break-word;}</style></head><body>${previewBody}</body></html>`
                      : '<p style="color:#94a3b8;font-style:italic;padding:16px;">Preview will appear here...</p>'
                  }
                  className="w-full h-64 border-0"
                />
              </div>
            </div>

            {/* Detected Variables Tags */}
            {detectedVars.length > 0 && (
              <div className="border-t border-slate-100 pt-3 space-y-1.5">
                <label className="text-xs font-medium text-slate-500">
                  Detected Template Variables
                </label>
                <div className="flex flex-wrap gap-1">
                  {detectedVars.map((v) => (
                    <span
                      key={v}
                      className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-mono text-slate-700"
                    >
                      {v}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
