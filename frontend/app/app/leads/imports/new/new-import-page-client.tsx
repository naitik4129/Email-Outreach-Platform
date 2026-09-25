"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, UploadCloud } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { createImport, uploadImportFile } from "@/lib/imports-api";
import {
  LEAD_IMPORT_FIELDS,
  SUPPRESSION_IMPORT_FIELDS,
  autoMapHeaders,
  duplicateMappedFields,
  toApiColumns,
} from "@/lib/lead-fields";
import { listLeadLists } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { ImportKind, ImportUploadOut } from "@/types/domain";

type Step = "UPLOAD" | "MAP" | "CONFIRM";

export function NewImportPageClient() {
  const router = useRouter();
  const { activeWorkspaceId } = useWorkspace();

  const [step, setStep] = useState<Step>("UPLOAD");
  const [file, setFile] = useState<File | null>(null);
  const [importKind, setImportKind] = useState<ImportKind>("LEADS");
  const [listId, setListId] = useState<string>("");
  const [uploadResult, setUploadResult] = useState<ImportUploadOut | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const mappableFields =
    importKind === "LEADS" ? LEAD_IMPORT_FIELDS : SUPPRESSION_IMPORT_FIELDS;

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", "picker"],
    queryFn: () => listLeadLists(activeWorkspaceId!, { limit: 100 }),
    enabled: Boolean(activeWorkspaceId),
  });

  const uploadMutation = useMutation({
    mutationFn: () => uploadImportFile(activeWorkspaceId!, file!),
    onSuccess: (data) => {
      setUploadResult(data);
      setMapping(autoMapHeaders(data.headers, mappableFields));
      setStep("MAP");
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Failed to upload file");
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createImport(activeWorkspaceId!, {
        storage_object_key: uploadResult!.storage_object_key,
        storage_object_version: uploadResult!.storage_object_version,
        storage_object_digest: uploadResult!.storage_object_digest,
        import_kind: importKind,
        mapping: {
          // The UI maps header -> field; the API expects field -> header.
          columns: toApiColumns(mapping),
          source_filename: file!.name,
        },
        list_id: listId || null,
      }),
    onSuccess: (job) => {
      router.push(`/app/leads/imports/${job.id}`);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : "Failed to start import");
    },
  });

  const handleUpload = (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError("Please select a file.");
      return;
    }
    uploadMutation.mutate();
  };

  const handleMappingSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Validate mapping: at least 'email' must be mapped
    if (!Object.values(mapping).includes("email")) {
      setError("You must map at least one column to 'email'.");
      return;
    }
    // The server takes one source column per field, so a shared target would
    // silently drop one of the columns.
    const duplicates = duplicateMappedFields(mapping);
    if (duplicates.length > 0) {
      const labels = duplicates.map(
        (key) => mappableFields.find((field) => field.key === key)?.label ?? key,
      );
      setError(`Each field can be mapped to one column only: ${labels.join(", ")}.`);
      return;
    }
    setStep("CONFIRM");
    setError(null);
  };

  const handleConfirm = () => {
    createMutation.mutate();
  };

  return (
    <main className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-normal text-slate-950">
          New Import
        </h1>
        <p className="mt-2 text-sm text-slate-500">
          Upload a CSV to import leads or manual suppressions.
        </p>
      </div>

      {error ? <Alert>{error}</Alert> : null}

      {step === "UPLOAD" && (
        <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm">
          <form onSubmit={handleUpload} className="space-y-6">
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-sm font-medium text-slate-700">Import Type</label>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={importKind === "LEADS"}
                      onChange={() => setImportKind("LEADS")}
                      className="text-teal-600 focus:ring-teal-600"
                    />
                    Leads
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="radio"
                      checked={importKind === "SUPPRESSION"}
                      onChange={() => setImportKind("SUPPRESSION")}
                      className="text-teal-600 focus:ring-teal-600"
                    />
                    Suppressions
                  </label>
                </div>
              </div>

              {importKind === "LEADS" && (
                <div className="space-y-1.5">
                  <label htmlFor="list-select" className="text-sm font-medium text-slate-700">
                    Add to List (Optional)
                  </label>
                  <select
                    id="list-select"
                    value={listId}
                    onChange={(e) => setListId(e.target.value)}
                    className="block w-full rounded-md border-slate-200 text-sm focus:border-teal-600 focus:ring-teal-600"
                  >
                    <option value="">No List</option>
                    {(listsQuery.data?.items ?? []).map((l) => (
                      <option key={l.id} value={l.id}>{l.name}</option>
                    ))}
                  </select>
                </div>
              )}

              <div className="space-y-1.5">
                <label className="text-sm font-medium text-slate-700">CSV File</label>
                <div className="flex justify-center rounded-lg border border-dashed border-slate-300 px-6 py-10">
                  <div className="text-center">
                    <UploadCloud className="mx-auto h-12 w-12 text-slate-300" aria-hidden="true" />
                    <div className="mt-4 flex text-sm leading-6 text-slate-600">
                      <label
                        htmlFor="file-upload"
                        className="relative cursor-pointer rounded-md bg-white font-semibold text-teal-600 focus-within:outline-none focus-within:ring-2 focus-within:ring-teal-600 focus-within:ring-offset-2 hover:text-teal-500"
                      >
                        <span>Upload a file</span>
                        <input
                          id="file-upload"
                          name="file-upload"
                          type="file"
                          accept=".csv"
                          className="sr-only"
                          onChange={(e) => setFile(e.target.files?.[0] || null)}
                        />
                      </label>
                      <p className="pl-1">or drag and drop</p>
                    </div>
                    <p className="text-xs leading-5 text-slate-500">
                      {file ? file.name : "CSV up to 10MB"}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex justify-end">
              <Button type="submit" disabled={uploadMutation.isPending || !file}>
                {uploadMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Upload & Next
              </Button>
            </div>
          </form>
        </section>
      )}

      {step === "MAP" && uploadResult && (
        <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm space-y-6">
          <div>
            <h2 className="text-lg font-medium text-slate-900">Map Columns</h2>
            <p className="text-sm text-slate-500">
              Found {uploadResult.detected_total_rows} rows. Map your CSV headers to system fields.
            </p>
          </div>

          <form onSubmit={handleMappingSubmit} className="space-y-6">
            <div className="divide-y divide-slate-100 border-t border-b border-slate-100">
              {uploadResult.headers.map((header) => (
                <div key={header} className="grid grid-cols-3 items-center gap-4 py-4">
                  <div className="col-span-1 text-sm font-medium text-slate-900">
                    {header}
                  </div>
                  <div className="col-span-2">
                    <select
                      value={mapping[header] || ""}
                      onChange={(e) => {
                        const val = e.target.value;
                        setMapping((prev) => {
                          const next = { ...prev };
                          if (val) next[header] = val;
                          else delete next[header];
                          return next;
                        });
                      }}
                      className="block w-full rounded-md border-slate-200 text-sm focus:border-teal-600 focus:ring-teal-600"
                    >
                      <option value="">-- Ignore --</option>
                      {mappableFields.map((field) => (
                        <option key={field.key} value={field.key}>
                          {field.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex justify-between">
              <Button type="button" variant="ghost" onClick={() => setStep("UPLOAD")}>
                Back
              </Button>
              <Button type="submit">
                Continue to Confirm
              </Button>
            </div>
          </form>
        </section>
      )}

      {step === "CONFIRM" && uploadResult && (
        <section className="rounded-md border border-slate-200 bg-white p-6 shadow-sm space-y-6">
          <div>
            <h2 className="text-lg font-medium text-slate-900">Confirm Import</h2>
            <p className="text-sm text-slate-500">
              Review your import details before starting.
            </p>
          </div>

          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="border-t border-slate-100 pt-4">
              <dt className="text-sm font-medium text-slate-500">File</dt>
              <dd className="mt-1 text-sm text-slate-900">{file?.name}</dd>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <dt className="text-sm font-medium text-slate-500">Total Rows</dt>
              <dd className="mt-1 text-sm text-slate-900">{uploadResult.detected_total_rows}</dd>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <dt className="text-sm font-medium text-slate-500">Import Kind</dt>
              <dd className="mt-1 text-sm text-slate-900">{importKind}</dd>
            </div>
            <div className="border-t border-slate-100 pt-4">
              <dt className="text-sm font-medium text-slate-500">Mapped Columns</dt>
              <dd className="mt-1 text-sm text-slate-900">
                {Object.keys(mapping).length} of {uploadResult.headers.length}
              </dd>
            </div>
          </dl>

          <div className="flex justify-between pt-4">
            <Button type="button" variant="ghost" onClick={() => setStep("MAP")}>
              Back to Mapping
            </Button>
            <Button onClick={handleConfirm} disabled={createMutation.isPending}>
              {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Start Import
            </Button>
          </div>
        </section>
      )}
    </main>
  );
}
