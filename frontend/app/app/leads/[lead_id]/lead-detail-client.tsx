"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Loader2, Pencil } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  LeadProfileDetails,
  LeadProfileFields,
} from "@/components/leads/lead-profile-fields";
import { ApiError } from "@/lib/api-client";
import {
  profilePayload,
  profileValuesFromLead,
  type LeadProfileFormValues,
} from "@/lib/lead-fields";
import { archiveLead, getLead, updateLead } from "@/lib/leads-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { LeadDetail } from "@/types/domain";

type EditState = {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  title: string;
  profile: LeadProfileFormValues;
  custom_fields: string;
};

function canManageContacts(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER" || role === "MEMBER";
}

function fullName(lead: LeadDetail) {
  return [lead.first_name, lead.last_name].filter(Boolean).join(" ") || "Unnamed lead";
}

function editStateFromLead(lead: LeadDetail): EditState {
  return {
    email: lead.email,
    first_name: lead.first_name ?? "",
    last_name: lead.last_name ?? "",
    company: lead.company ?? "",
    title: lead.title ?? "",
    profile: profileValuesFromLead(lead),
    custom_fields: JSON.stringify(lead.custom_fields, null, 2),
  };
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

export function LeadDetailClient({ leadId }: { leadId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<EditState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mayManage = canManageContacts(activeWorkspace?.role_code);

  const leadQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
    queryFn: () => getLead(activeWorkspaceId!, leadId),
    enabled: Boolean(activeWorkspaceId),
  });

  useEffect(() => {
    if (leadQuery.data && !editing) {
      setForm(editStateFromLead(leadQuery.data));
    }
  }, [leadQuery.data, editing]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!form || !leadQuery.data) throw new Error("Lead is not loaded");
      let customFields: Record<string, unknown>;
      try {
        const parsed: unknown = JSON.parse(form.custom_fields || "{}");
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
          throw new Error("Custom fields must be an object");
        }
        customFields = parsed as Record<string, unknown>;
      } catch {
        throw new ApiError("Custom fields must be valid JSON object", 422, "validation_error", null);
      }
      return updateLead(activeWorkspaceId!, leadId, {
        email: form.email,
        first_name: form.first_name || null,
        last_name: form.last_name || null,
        company: form.company || null,
        title: form.title || null,
        ...profilePayload(form.profile),
        custom_fields: customFields,
        expected_version: leadQuery.data.version,
      });
    },
    onSuccess: () => {
      setEditing(false);
      setError(null);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setError("This lead changed elsewhere. Reload the latest version and retry.");
        queryClient.invalidateQueries({
          queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
        });
      } else {
        setError(errorMessage(err));
      }
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveLead(activeWorkspaceId!, leadId, leadQuery.data!.version),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
      router.push("/app/leads");
    },
    onError: (err) => setError(errorMessage(err)),
  });

  if (leadQuery.isLoading) {
    return (
      <main className="grid min-h-80 place-items-center">
        <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
      </main>
    );
  }

  if (leadQuery.error) {
    return (
      <main className="space-y-4">
        <Button asChild variant="ghost">
          <Link href="/app/leads">Back to leads</Link>
        </Button>
        <Alert>{errorMessage(leadQuery.error)}</Alert>
      </main>
    );
  }

  const lead = leadQuery.data;
  if (!lead || !form) return null;

  return (
    <main className="space-y-6">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <Button asChild variant="ghost">
            <Link href="/app/leads">Back to leads</Link>
          </Button>
          <h1 className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
            {fullName(lead)}
          </h1>
          <p className="mt-2 text-sm text-slate-500">{lead.email}</p>
        </div>
        {mayManage && lead.status === "ACTIVE" ? (
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil className="h-4 w-4" aria-hidden="true" />
              Edit
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={archiveMutation.isPending}
              onClick={() => archiveMutation.mutate()}
            >
              <Archive className="h-4 w-4" aria-hidden="true" />
              Archive
            </Button>
          </div>
        ) : null}
      </div>

      {error ? <Alert>{error}</Alert> : null}

      <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-semibold tracking-normal text-slate-950">
          Profile
        </h2>
        {editing && mayManage ? (
          <form
            className="mt-4 grid gap-4 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              setError(null);
              saveMutation.mutate();
            }}
          >
            <Field id="edit-email" label="Email" className="md:col-span-2">
              <Input
                value={form.email}
                onChange={(event) => setForm({ ...form, email: event.target.value })}
                disabled={saveMutation.isPending}
                required
                type="email"
              />
            </Field>
            <Field id="edit-first-name" label="First name">
              <Input
                value={form.first_name}
                onChange={(event) =>
                  setForm({ ...form, first_name: event.target.value })
                }
                disabled={saveMutation.isPending}
              />
            </Field>
            <Field id="edit-last-name" label="Last name">
              <Input
                value={form.last_name}
                onChange={(event) =>
                  setForm({ ...form, last_name: event.target.value })
                }
                disabled={saveMutation.isPending}
              />
            </Field>
            <Field id="edit-company" label="Company">
              <Input
                value={form.company}
                onChange={(event) => setForm({ ...form, company: event.target.value })}
                disabled={saveMutation.isPending}
              />
            </Field>
            <Field id="edit-title" label="Job title">
              <Input
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                disabled={saveMutation.isPending}
              />
            </Field>
            <LeadProfileFields
              idPrefix="edit"
              values={form.profile}
              onChange={(key, value) =>
                setForm((current) =>
                  current
                    ? { ...current, profile: { ...current.profile, [key]: value } }
                    : current,
                )
              }
              disabled={saveMutation.isPending}
              defaultOpen
            />
            <div className="space-y-1.5 md:col-span-2">
              <label
                htmlFor="edit-custom-fields"
                className="text-sm font-medium text-slate-700"
              >
                Custom fields
              </label>
              <textarea
                id="edit-custom-fields"
                value={form.custom_fields}
                onChange={(event) =>
                  setForm({ ...form, custom_fields: event.target.value })
                }
                disabled={saveMutation.isPending}
                rows={6}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
              />
            </div>
            <div className="flex items-center gap-2 md:col-span-2">
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : null}
                Save
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setEditing(false);
                  setForm(editStateFromLead(lead));
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : (
          <dl className="mt-4 grid gap-4 text-sm md:grid-cols-2">
            <div>
              <dt className="font-medium text-slate-500">Email</dt>
              <dd className="mt-1 text-slate-950">{lead.email}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500">Status</dt>
              <dd className="mt-1 text-slate-950">{lead.status}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500">Company</dt>
              <dd className="mt-1 text-slate-950">{lead.company ?? "No company"}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500">Job title</dt>
              <dd className="mt-1 text-slate-950">{lead.title ?? "No title"}</dd>
            </div>
          </dl>
        )}
        {editing && mayManage ? null : <LeadProfileDetails lead={lead} />}
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-semibold tracking-normal text-slate-950">
          Lists
        </h2>
        {lead.lists.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">No list memberships.</p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {lead.lists.map((list) => (
              <Link
                key={list.id}
                href={`/app/leads/lists/${list.id}`}
                className="rounded-md border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                {list.name}
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
