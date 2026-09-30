"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { errorMessage } from "@/lib/errors";
import { listLeads } from "@/lib/leads-api";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { useWorkspace } from "@/lib/workspace-context";

type LeadPickerProps = {
  enabled: boolean;
  search: string;
  onSearchChange: (value: string) => void;
  // Held by the parent so a selection survives searching and stepping back.
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  disabled?: boolean;
};

function fullName(firstName: string | null, lastName: string | null) {
  return [firstName, lastName].filter(Boolean).join(" ") || "Unnamed lead";
}

// Search-and-tick list of ACTIVE leads (archived ones can't join a list).
export function LeadPicker({
  enabled,
  search,
  onSearchChange,
  selected,
  onSelectedChange,
  disabled = false,
}: LeadPickerProps) {
  const { activeWorkspaceId } = useWorkspace();
  const debouncedSearch = useDebouncedValue(search, 350);

  const query = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "leads", "list-picker", debouncedSearch],
    queryFn: () =>
      listLeads(activeWorkspaceId!, { limit: 25, q: debouncedSearch, status: "ACTIVE" }),
    enabled: Boolean(activeWorkspaceId && enabled),
  });

  const leads = query.data?.items ?? [];
  const allShownSelected = leads.length > 0 && leads.every((lead) => selected.has(lead.id));

  function setMany(ids: string[], checked: boolean) {
    const next = new Set(selected);
    for (const id of ids) {
      if (checked) next.add(id);
      else next.delete(id);
    }
    onSelectedChange(next);
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400"
          aria-hidden="true"
        />
        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search active leads by name, email or company"
          aria-label="Search leads"
          className="pl-9"
          disabled={disabled}
          autoFocus
        />
      </div>

      {query.isLoading ? (
        <div className="flex h-32 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
        </div>
      ) : query.error ? (
        <p className="py-6 text-center text-sm text-red-600">{errorMessage(query.error)}</p>
      ) : leads.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-500">
          {debouncedSearch ? "No active leads match that search." : "No active leads yet."}
        </p>
      ) : (
        <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
          <label className="flex items-center gap-3 bg-slate-50 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <input
              type="checkbox"
              checked={allShownSelected}
              disabled={disabled}
              onChange={(event) =>
                setMany(
                  leads.map((lead) => lead.id),
                  event.target.checked,
                )
              }
              className="h-4 w-4 rounded border-slate-300 text-brand-600"
            />
            Select all shown
          </label>
          {leads.map((lead) => (
            <label
              key={lead.id}
              className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-slate-50"
            >
              <input
                type="checkbox"
                checked={selected.has(lead.id)}
                disabled={disabled}
                onChange={(event) => setMany([lead.id], event.target.checked)}
                className="h-4 w-4 rounded border-slate-300 text-brand-600"
              />
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-slate-900">
                  {fullName(lead.first_name, lead.last_name)}
                </span>
                <span className="block text-slate-500">
                  {lead.email}
                  {lead.company ? ` · ${lead.company}` : ""}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
