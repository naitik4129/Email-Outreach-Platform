"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { LeadProfileFields } from "@/components/leads/lead-profile-fields";
import { errorMessage } from "@/lib/errors";
import {
  emptyProfileValues,
  profilePayload,
  type LeadProfileFormValues,
} from "@/lib/lead-fields";
import { addLeadListMember, createLead, listLeadLists } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";

type AddLeadDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Pre-checks a list (e.g. opened from that list's own detail page) without
  // stopping the user from also adding the lead to other lists.
  defaultListId?: string;
};

type FormState = {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  title: string;
  profile: LeadProfileFormValues;
};

function emptyForm(): FormState {
  return {
    email: "",
    first_name: "",
    last_name: "",
    company: "",
    title: "",
    profile: emptyProfileValues,
  };
}

export function AddLeadDialog({ open, onOpenChange, defaultListId }: AddLeadDialogProps) {
  const queryClient = useQueryClient();
  const { activeWorkspaceId } = useWorkspace();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [selectedListIds, setSelectedListIds] = useState<Set<string>>(
    () => new Set(defaultListId ? [defaultListId] : []),
  );
  const [error, setError] = useState<string | null>(null);

  const listsQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead-lists", "picker"],
    queryFn: () => listLeadLists(activeWorkspaceId!, { limit: 100 }),
    enabled: Boolean(activeWorkspaceId) && open,
  });

  function reset() {
    setForm(emptyForm());
    setSelectedListIds(new Set(defaultListId ? [defaultListId] : []));
    setError(null);
  }

  function close() {
    reset();
    onOpenChange(false);
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      const lead = await createLead(activeWorkspaceId!, {
        email: form.email,
        first_name: form.first_name || null,
        last_name: form.last_name || null,
        company: form.company || null,
        title: form.title || null,
        ...profilePayload(form.profile),
        custom_fields: {},
      });
      // A handful of lists at most, so N sequential API calls (not a bulk
      // endpoint) is fine here -- see bulk-add-to-list.ts for the version
      // used when adding many leads to a list at once.
      await Promise.all(
        [...selectedListIds].map((listId) =>
          addLeadListMember(activeWorkspaceId!, listId, lead.id),
        ),
      );
      return lead;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", activeWorkspaceId, "leads"] });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
      close();
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <Dialog
      open={open}
      onRequestClose={() => {
        if (!createMutation.isPending) close();
      }}
      title="Add lead"
      align="center"
      className="max-w-2xl"
      footer={
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={createMutation.isPending}
            onClick={close}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            form="add-lead-form"
            disabled={createMutation.isPending || !form.email}
          >
            {createMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : null}
            Create lead
          </Button>
        </div>
      }
    >
      <form
        id="add-lead-form"
        className="grid gap-4 p-4 md:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          setError(null);
          createMutation.mutate();
        }}
      >
        {error ? (
          <div className="md:col-span-2">
            <Alert>{error}</Alert>
          </div>
        ) : null}
        <Field id="add-lead-email" label="Email" className="md:col-span-2">
          <Input
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            disabled={createMutation.isPending}
            required
            type="email"
            autoFocus
          />
        </Field>
        <Field id="add-lead-first-name" label="First name">
          <Input
            value={form.first_name}
            onChange={(event) => setForm({ ...form, first_name: event.target.value })}
            disabled={createMutation.isPending}
          />
        </Field>
        <Field id="add-lead-last-name" label="Last name">
          <Input
            value={form.last_name}
            onChange={(event) => setForm({ ...form, last_name: event.target.value })}
            disabled={createMutation.isPending}
          />
        </Field>
        <Field id="add-lead-company" label="Company">
          <Input
            value={form.company}
            onChange={(event) => setForm({ ...form, company: event.target.value })}
            disabled={createMutation.isPending}
          />
        </Field>
        <Field id="add-lead-title" label="Job title">
          <Input
            value={form.title}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
            disabled={createMutation.isPending}
          />
        </Field>

        <LeadProfileFields
          idPrefix="add-lead"
          values={form.profile}
          onChange={(key, value) =>
            setForm((current) => ({
              ...current,
              profile: { ...current.profile, [key]: value },
            }))
          }
          disabled={createMutation.isPending}
        />

        <fieldset className="md:col-span-2">
          <legend className="text-sm font-medium text-slate-700">
            Add to list{listsQuery.data?.items.length ? "(s)" : ""}
          </legend>
          {listsQuery.data?.items.length ? (
            <div className="mt-2 flex flex-wrap gap-3">
              {listsQuery.data.items.map((list) => (
                <label
                  key={list.id}
                  className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-700"
                >
                  <input
                    type="checkbox"
                    checked={selectedListIds.has(list.id)}
                    disabled={createMutation.isPending}
                    onChange={(event) =>
                      setSelectedListIds((current) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(list.id);
                        else next.delete(list.id);
                        return next;
                      })
                    }
                    className="h-4 w-4 rounded border-slate-300 text-brand-600"
                  />
                  {list.name}
                </label>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-sm text-slate-500">
              No lists yet. You can add this lead to a list later.
            </p>
          )}
        </fieldset>
      </form>
    </Dialog>
  );
}
