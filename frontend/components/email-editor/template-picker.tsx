"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Loader2, Search } from "lucide-react";

import { Popover } from "@/components/ui/popover";
import { ApiError } from "@/lib/api-client";
import { getTemplate, listTemplates } from "@/lib/templates-api";

export type PickedTemplate = {
  templateId: string;
  templateVersionId: string | null;
  name: string;
  subject: string;
  bodyHtml: string;
  preheader: string;
};

type Props = {
  workspaceId: string | null;
  disabled?: boolean;
  onPick: (template: PickedTemplate) => void;
};

function useDebounced<T>(value: T, ms: number) {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

export function TemplatePicker({ workspaceId, disabled, onPick }: Props) {
  const [query, setQuery] = React.useState("");
  const [loadingId, setLoadingId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const debounced = useDebounced(query, 250);

  const templatesQuery = useQuery({
    queryKey: ["workspace", workspaceId, "templates", "picker", debounced],
    queryFn: () =>
      listTemplates(workspaceId as string, { status: "ACTIVE", q: debounced || null, limit: 20 }),
    enabled: Boolean(workspaceId),
  });

  async function pick(templateId: string, close: () => void) {
    if (!workspaceId) return;
    setLoadingId(templateId);
    setError(null);
    try {
      const template = await getTemplate(workspaceId, templateId);
      if (template.archived_at) {
        setError("That template was archived. Choose another one.");
        void templatesQuery.refetch();
        return;
      }
      onPick({
        templateId,
        templateVersionId: template.current_version_id,
        name: template.name,
        subject: template.subject,
        bodyHtml: template.body_html,
        preheader: template.preheader ?? "",
      });
      close();
    } catch (err) {
      // A template deleted/archived after the list loaded lands here.
      setError(
        err instanceof ApiError && err.status === 404
          ? "That template no longer exists."
          : "We couldn’t load that template. Please try again.",
      );
      void templatesQuery.refetch();
    } finally {
      setLoadingId(null);
    }
  }

  const items = templatesQuery.data?.items ?? [];

  return (
    <Popover
      label="Choose a template"
      className="w-80"
      trigger={({ toggle, ariaProps }) => (
        <button
          type="button"
          onClick={toggle}
          disabled={disabled}
          {...ariaProps}
          className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:pointer-events-none disabled:opacity-40"
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
          Template
        </button>
      )}
    >
      {(close) => (
        <div className="space-y-2">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-slate-400"
              aria-hidden="true"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search templates"
              aria-label="Search templates"
              className="h-8 w-full rounded-md border border-slate-200 pl-8 pr-2 text-sm"
            />
          </div>
          {error ? (
            <p role="alert" className="text-xs text-red-600">
              {error}
            </p>
          ) : null}
          <div className="max-h-64 overflow-y-auto">
            {templatesQuery.isLoading ? (
              <div className="flex justify-center p-4">
                <Loader2 className="h-4 w-4 animate-spin text-slate-400" aria-label="Loading" />
              </div>
            ) : templatesQuery.isError ? (
              <p className="p-2 text-sm text-red-600">Couldn&apos;t load templates.</p>
            ) : items.length === 0 ? (
              <p className="p-2 text-sm text-slate-500">
                {debounced ? "No templates match your search." : "You have no templates yet."}
              </p>
            ) : (
              <ul>
                {items.map((template) => (
                  <li key={template.id}>
                    <button
                      type="button"
                      disabled={loadingId !== null}
                      onClick={() => void pick(template.id, close)}
                      className="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-slate-100 disabled:opacity-60"
                    >
                      <span className="flex items-center justify-between text-sm font-medium text-slate-900">
                        {template.name}
                        {loadingId === template.id ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-label="Loading" />
                        ) : null}
                      </span>
                      {template.subject ? (
                        <span className="truncate text-xs text-slate-500">{template.subject}</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Popover>
  );
}
