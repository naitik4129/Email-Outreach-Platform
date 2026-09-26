"use client";

import Link from "next/link";
import { AlertTriangle, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

import type {
  PreviewRecipient,
  PreviewState,
} from "@/components/email-editor/use-email-preview";
import { Select } from "@/components/ui/select";

export type FromOption = { id: string; label: string };

type Props = {
  preview: PreviewState;
  subjectIsEmpty: boolean;
  bodyIsEmpty: boolean;
  fromOptions: FromOption[];
  fromId: string;
  onFromChange: (id: string) => void;
  sendersHref: string;
  recipients: PreviewRecipient[];
  recipientIndex: number;
  onRecipientIndexChange: (index: number) => void;
  usingSample: boolean;
  recipientsError: string | null;
  // Total prospects in the audience when known (recipients may be a page of it).
  totalRecipients?: number | null;
  // Lets the caller rewrite the sanitized preview body (e.g. cid: image URLs).
  mapBody?: (html: string) => string;
};

function buildSrcDoc(bodyHtml: string) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:16px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#0f172a}
img{max-width:100%;height:auto}p{margin:0 0 .75em}
</style></head><body>${bodyHtml}</body></html>`;
}

export function EmailPreviewPane({
  preview,
  subjectIsEmpty,
  bodyIsEmpty,
  fromOptions,
  fromId,
  onFromChange,
  sendersHref,
  recipients,
  recipientIndex,
  onRecipientIndexChange,
  usingSample,
  recipientsError,
  totalRecipients,
  mapBody,
}: Props) {
  const recipient = recipients[recipientIndex] ?? recipients[0];
  const data = preview.data;
  const body = data ? (mapBody ? mapBody(data.body_html) : data.body_html) : "";
  const counter =
    recipients.length > 0
      ? `${recipientIndex + 1} of ${totalRecipients ?? recipients.length}`
      : "";

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="grid grid-cols-[3.5rem_1fr] items-center gap-x-3 gap-y-2 text-sm">
        <label htmlFor="preview-from" className="font-medium text-slate-600">
          From
        </label>
        {fromOptions.length === 0 ? (
          <p className="text-slate-500" id="preview-from">
            No email account connected.{" "}
            <Link href={sendersHref} className="font-medium text-indigo-600 hover:underline">
              Assign a sender
            </Link>
          </p>
        ) : (
          <Select
            id="preview-from"
            value={fromId}
            onChange={(event) => onFromChange(event.target.value)}
          >
            {fromOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </Select>
        )}

        <label htmlFor="preview-to" className="font-medium text-slate-600">
          To
        </label>
        <div className="flex items-center gap-1.5">
          <Select
            id="preview-to"
            value={String(recipientIndex)}
            onChange={(event) => onRecipientIndexChange(Number(event.target.value))}
            aria-describedby="preview-to-hint"
          >
            {recipients.map((item, index) => (
              <option key={item.id} value={index}>
                {item.name ? `${item.name} <${item.email}>` : item.email}
              </option>
            ))}
          </Select>
          <button
            type="button"
            aria-label="Previous prospect"
            disabled={recipientIndex <= 0}
            onClick={() => onRecipientIndexChange(recipientIndex - 1)}
            className="inline-flex h-10 w-9 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="Next prospect"
            disabled={recipientIndex >= recipients.length - 1}
            onClick={() => onRecipientIndexChange(recipientIndex + 1)}
            className="inline-flex h-10 w-9 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>
      <p id="preview-to-hint" className="-mt-1 text-xs text-slate-500">
        {usingSample
          ? "Sample data: add prospects to this campaign’s audience to preview real ones."
          : `Prospect ${counter}. Personalization uses this prospect’s data.`}
        {recipientsError ? (
          <span className="ml-1 text-amber-700">{recipientsError}</span>
        ) : null}
      </p>

      <div className="flex min-h-0 flex-1 flex-col rounded-md border border-slate-200 bg-white">
        <div className="space-y-1 border-b border-slate-200 p-3 text-sm">
          <p>
            <span className="font-semibold text-slate-900">To:</span>{" "}
            {recipient
              ? recipient.name
                ? `${recipient.name} <${recipient.email}>`
                : recipient.email
              : ""}
          </p>
          <p>
            <span className="font-semibold text-slate-900">Subject:</span>{" "}
            {subjectIsEmpty ? (
              <span className="text-slate-400">(no subject)</span>
            ) : (
              (data?.subject ?? "")
            )}
          </p>
          {data?.preheader ? (
            <p className="text-xs text-slate-500">
              <span className="font-semibold">Pre-header:</span> {data.preheader}
            </p>
          ) : null}
        </div>
        <div className="relative min-h-[16rem] flex-1">
          {preview.isLoading ? (
            <Loader2
              className="absolute right-3 top-3 h-4 w-4 animate-spin text-slate-400"
              aria-label="Updating preview"
            />
          ) : null}
          {preview.error ? (
            <p className="flex items-start gap-2 p-4 text-sm text-red-700" role="alert">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {preview.error}
            </p>
          ) : bodyIsEmpty ? (
            <p className="p-4 text-sm text-slate-400">The email body is empty.</p>
          ) : (
            <iframe
              title="Email preview"
              sandbox=""
              srcDoc={buildSrcDoc(body)}
              className="h-full min-h-[16rem] w-full rounded-b-md"
            />
          )}
        </div>
      </div>
    </div>
  );
}
