"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Eye, FileText, Loader2, Sparkles } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createTemplate, previewTemplate } from "@/lib/templates-api";
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

export default function NewTemplatePage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();

  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [bodyHtml, setBodyHtml] = useState("<p>Hi {{first_name|there}},</p>\n\n<p>I noticed your work at {{company}} and wanted to reach out.</p>");
  const [activeField, setActiveField] = useState<"subject" | "body">("body");
  const [activeTab, setActiveTab] = useState<"editor" | "preview">("editor");
  const [formError, setFormError] = useState<string | null>(null);

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

  const createMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return createTemplate(activeWorkspaceId, {
        name,
        subject,
        body_html: bodyHtml,
      });
    },
    onSuccess: (newTmpl) => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "templates"],
      });
      router.push(`/app/templates/${newTmpl.id}`);
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        setFormError(err.message);
      } else {
        setFormError("Failed to create template. Please check your inputs.");
      }
    },
  });

  // Debounced preview calculation
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

  function handleSubmit(e: React.FormEvent) {
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
    createMutation.mutate();
  }

  if (!mayManage) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/app/templates">
              <ArrowLeft className="mr-1.5 h-4 w-4" />
              Back to templates
            </Link>
          </Button>
        </div>
        <Alert variant="error">
          You do not have permission to create templates in this workspace.
        </Alert>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/app/templates">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-slate-900">
                New Email Template
              </h1>
              <span className="inline-flex items-center rounded-md bg-indigo-50 px-2 py-0.5 text-xs font-semibold text-indigo-700">
                Standard Template
              </span>
            </div>
            <p className="text-xs text-slate-500">
              Reusable outreach content with deterministic variables and safe rendering.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" asChild>
            <Link href="/app/templates">Cancel</Link>
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              "Save Template"
            )}
          </Button>
        </div>
      </div>

      {formError && (
        <Alert variant="error">
          <span>{formError}</span>
        </Alert>
      )}

      {/* Main Grid: Form + Live Preview */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Editor Column */}
        <div className="space-y-5 lg:col-span-7">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            <Field label="Template Name" required>
              <Input
                placeholder="e.g. Cold Outreach Sequence - Step 1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={200}
                required
              />
            </Field>

            <Field label="Subject Line" required>
              <Input
                ref={subjectRef}
                placeholder="e.g. Quick question for {{first_name|there}}"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                onFocus={() => setActiveField("subject")}
                maxLength={500}
                required
              />
            </Field>

            {/* Variable Pills Toolbar */}
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

            <Field label="Email Body (HTML/Text)" required>
              <textarea
                ref={bodyRef}
                rows={12}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                placeholder="<p>Hi {{first_name}},</p><p>...</p>"
                value={bodyHtml}
                onChange={(e) => setBodyHtml(e.target.value)}
                onFocus={() => setActiveField("body")}
                maxLength={200000}
              />
            </Field>
          </div>
        </div>

        {/* Preview Column */}
        <div className="space-y-4 lg:col-span-5">
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Eye className="h-4 w-4 text-indigo-600" />
                <h2 className="text-sm font-semibold text-slate-900">
                  Live Preview (Sample Lead)
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
                {previewSubject || <span className="italic text-slate-400">Enter a subject...</span>}
              </div>
            </div>

            {/* Rendered Body Sandbox */}
            <div>
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                Rendered Body (Isolated Sandbox)
              </label>
              <div className="mt-1 rounded-md border border-slate-200 bg-white overflow-hidden shadow-inner min-h-[220px]">
                <iframe
                  title="Live Template Preview"
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
