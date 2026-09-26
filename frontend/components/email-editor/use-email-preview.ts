"use client";

import * as React from "react";

import { ApiError } from "@/lib/api-client";
import { previewTemplate } from "@/lib/templates-api";
import type { TemplatePreviewResult } from "@/types/domain";

export type PreviewRecipient = {
  id: string;
  email: string;
  name: string;
  // Real recipients carry the audience member's frozen variables, exactly what
  // the message will render with. null = let the server use its sample data.
  variables: Record<string, unknown> | null;
};

export const SAMPLE_RECIPIENT: PreviewRecipient = {
  id: "sample",
  email: "alex.taylor@acme.example.com",
  name: "Alex Taylor",
  variables: null,
};

export type PreviewState = {
  data: TemplatePreviewResult | null;
  error: string | null;
  isLoading: boolean;
};

type Input = {
  workspaceId: string | null;
  subject: string;
  preheader: string;
  bodyHtml: string;
  recipient: PreviewRecipient;
  enabled?: boolean;
  debounceMs?: number;
};

// Shown when the subject is blank: the preview endpoint requires a subject, and
// an empty one is flagged separately by the caller.
const EMPTY_SUBJECT_PLACEHOLDER = "(no subject)";

export function useEmailPreview({
  workspaceId,
  subject,
  preheader,
  bodyHtml,
  recipient,
  enabled = true,
  debounceMs = 400,
}: Input): PreviewState {
  const [state, setState] = React.useState<PreviewState>({
    data: null,
    error: null,
    isLoading: false,
  });
  const requestId = React.useRef(0);
  const variablesKey = recipient.variables ? JSON.stringify(recipient.variables) : "";

  React.useEffect(() => {
    if (!enabled || !workspaceId) return;
    const current = ++requestId.current;
    setState((prev) => ({ ...prev, isLoading: true }));
    const timer = setTimeout(async () => {
      try {
        const data = await previewTemplate(workspaceId, {
          subject: subject.trim() || EMPTY_SUBJECT_PLACEHOLDER,
          body_html: bodyHtml,
          preheader: preheader.trim() || null,
          sample_data: recipient.variables,
        });
        // Ignore responses that were overtaken by a newer edit.
        if (current === requestId.current) setState({ data, error: null, isLoading: false });
      } catch (error) {
        if (current !== requestId.current) return;
        const message =
          error instanceof ApiError
            ? error.message
            : "We couldn't render the preview. Please try again.";
        setState((prev) => ({ data: prev.data, error: message, isLoading: false }));
      }
    }, debounceMs);
    return () => clearTimeout(timer);
    // recipient.variables is tracked through variablesKey to avoid refetching on
    // identity changes only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, subject, preheader, bodyHtml, variablesKey, enabled, debounceMs]);

  return state;
}
