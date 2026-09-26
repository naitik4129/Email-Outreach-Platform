"use client";

import * as React from "react";
import { ImagePlus, Image as ImageIcon, Loader2, Paperclip, X } from "lucide-react";

import {
  ATTACHMENT_ACCEPT,
  formatBytes,
  IMAGE_ACCEPT,
  MAX_ATTACHMENTS,
  MAX_TOTAL_BYTES,
} from "@/components/campaigns/sequence/attachment-rules";
import { cn } from "@/lib/utils";
import type { StepAttachment } from "@/types/domain";

type UploadButtonsProps = {
  disabled: boolean;
  uploading: boolean;
  onFile: (file: File, disposition: "ATTACHMENT" | "INLINE") => void;
};

// Toolbar buttons: attach a file / insert an image. Each opens the browser's file
// picker; the upload itself is handled by the caller.
export function UploadButtons({ disabled, uploading, onFile }: UploadButtonsProps) {
  const fileRef = React.useRef<HTMLInputElement>(null);
  const imageRef = React.useRef<HTMLInputElement>(null);

  function handle(
    event: React.ChangeEvent<HTMLInputElement>,
    disposition: "ATTACHMENT" | "INLINE",
  ) {
    const file = event.target.files?.[0];
    // Reset so choosing the same file twice in a row still fires onChange.
    event.target.value = "";
    if (file) onFile(file, disposition);
  }

  const buttonClass =
    "inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:pointer-events-none disabled:opacity-40";

  return (
    <>
      <button
        type="button"
        aria-label="Attach a file"
        title="Attach a file"
        disabled={disabled || uploading}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => fileRef.current?.click()}
        className={buttonClass}
      >
        {uploading ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <Paperclip className="h-4 w-4" aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        aria-label="Insert an image"
        title="Insert an image"
        disabled={disabled || uploading}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => imageRef.current?.click()}
        className={buttonClass}
      >
        <ImagePlus className="h-4 w-4" aria-hidden="true" />
      </button>
      <input
        ref={fileRef}
        type="file"
        hidden
        accept={ATTACHMENT_ACCEPT}
        aria-label="Choose a file to attach"
        onChange={(event) => handle(event, "ATTACHMENT")}
      />
      <input
        ref={imageRef}
        type="file"
        hidden
        accept={IMAGE_ACCEPT}
        aria-label="Choose an image to insert"
        onChange={(event) => handle(event, "INLINE")}
      />
    </>
  );
}

type Props = {
  attachments: StepAttachment[];
  readOnly: boolean;
  removingId: string | null;
  onRemove: (attachment: StepAttachment) => void;
};

export function AttachmentsBar({ attachments, readOnly, removingId, onRemove }: Props) {
  if (attachments.length === 0) return null;
  const files = attachments.filter((a) => a.disposition === "ATTACHMENT");
  const totalBytes = attachments.reduce((sum, a) => sum + a.size_bytes, 0);

  return (
    <section aria-label="Attachments" className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <p className="mb-2 text-xs text-slate-500">
        {files.length} of {MAX_ATTACHMENTS} attachments · {formatBytes(totalBytes)} of{" "}
        {formatBytes(MAX_TOTAL_BYTES)}
      </p>
      <ul className="flex flex-wrap gap-2">
        {attachments.map((attachment) => (
          <li
            key={attachment.id}
            className={cn(
              "flex max-w-full items-center gap-2 rounded-md border bg-white px-2 py-1 text-xs",
              "border-slate-200 text-slate-700",
            )}
          >
            {attachment.disposition === "INLINE" ? (
              <ImageIcon className="h-3.5 w-3.5 shrink-0 text-indigo-500" aria-hidden="true" />
            ) : (
              <Paperclip className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
            )}
            <span className="truncate font-medium" title={attachment.filename}>
              {attachment.filename}
            </span>
            <span className="shrink-0 text-slate-400">
              {attachment.disposition === "INLINE" ? "in email · " : ""}
              {formatBytes(attachment.size_bytes)}
            </span>
            {!readOnly ? (
              <button
                type="button"
                aria-label={`Remove ${attachment.filename}`}
                disabled={removingId === attachment.id}
                onClick={() => onRemove(attachment)}
                className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-red-600 disabled:opacity-40"
              >
                {removingId === attachment.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                )}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
