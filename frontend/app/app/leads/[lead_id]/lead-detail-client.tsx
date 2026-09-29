"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, Eraser, Loader2, Pencil, RotateCcw } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { TypeToConfirmDialog } from "@/components/ui/type-to-confirm-dialog";
import {
  LeadProfileDetails,
  LeadProfileFields,
} from "@/components/leads/lead-profile-fields";
import { LeadActivityTimeline } from "@/components/leads/lead-activity";
import { LeadCampaignHistory } from "@/components/leads/lead-campaign-history";
import { ApiError } from "@/lib/api-client";
import {
  profilePayload,
  profileValuesFromLead,
  type LeadProfileFormValues,
} from "@/lib/lead-fields";
import { eraseLead } from "@/lib/erasure-api";
import { archiveLead, getLead, unarchiveLead, updateLead } from "@/lib/leads-api";
import { canEraseData, canManageContacts } from "@/lib/permissions";
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
  const mayErase = canEraseData(activeWorkspace?.role_code);
  const [confirmErase, setConfirmErase] = useState(false);
  const [eraseError, setEraseError] = useState<string | null>(null);

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

  const restoreMutation = useMutation({
    mutationFn: () => unarchiveLead(activeWorkspaceId!, leadId, leadQuery.data!.version),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const eraseMutation = useMutation({
    mutationFn: (phrase: string) => eraseLead(activeWorkspaceId!, leadId, phrase),
    onSuccess: () => {
      setConfirmErase(false);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead", leadId],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "leads"],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "lead-lists"],
      });
      router.push("/app/leads");
    },
    onError: (err) => setEraseError(errorMessage(err)),
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
      <PageHeader
        title={fullName(lead)}
        description={lead.email}
        back={{ href: "/app/leads", label: "Back to leads" }}
        actions={
          <>
            {mayManage && lead.status === "ACTIVE" ? (
              <>
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
              </>
            ) : null}
            {mayManage && lead.status === "ARCHIVED" && !lead.erased_at ? (
              <Button
                type="button"
                variant="outline"
                loading={restoreMutation.isPending}
                onClick={() => restoreMutation.mutate()}
              >
                <RotateCcw className="h-4 w-4" aria-hidden="true" />
                Restore
              </Button>
            ) : null}
          </>
        }
      />

      {error ? <Alert>{error}</Alert> : null}
      {lead.erased_at ? (
        <Alert variant="info">
          This person&apos;s data was erased. Only an empty record remains, so that their
          past emails still count in campaign results.
        </Alert>
      ) : null}

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <h2 className="text-lg font-semibold tracking-normal text-slate-900">
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
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
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
              <dd className="mt-1 text-slate-900">{lead.email}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500">Status</dt>
              <dd className="mt-1 text-slate-900">{lead.status}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500">Job title</dt>
              <dd className="mt-1 text-slate-900">{lead.title ?? "No title"}</dd>
            </div>
          </dl>
        )}
        {editing && mayManage ? null : (
          <LeadProfileDetails
            lead={lead}
            groups={["Professional", "Location"]}
            emptyMessage="No additional profile details."
          />
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <h2 className="text-lg font-semibold tracking-normal text-slate-900">
          Company
        </h2>
        {!editing || !mayManage ? (
          <>
            <dl className="mt-4 grid gap-4 text-sm md:grid-cols-2">
              <div>
                <dt className="font-medium text-slate-500">Company</dt>
                <dd className="mt-1 text-slate-900">{lead.company ?? "No company"}</dd>
              </div>
            </dl>
            <LeadProfileDetails
              lead={lead}
              groups={["Company"]}
              emptyMessage="No additional company details."
            />
          </>
        ) : (
          <p className="mt-4 text-sm text-slate-500">
            Company name and details are edited together with the profile above.
          </p>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
        <h2 className="text-lg font-semibold tracking-normal text-slate-900">
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

      {mayErase && !lead.erased_at ? (
        <section className="rounded-xl border border-red-200 bg-white p-5 shadow-card">
          <h2 className="text-lg font-semibold tracking-normal text-slate-900">
            Erase personal data
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Permanently erases this person&apos;s name, contact details, email content and
            replies everywhere in the workspace, and stops any emails still planned for them.
            If they unsubscribed or bounced, their address stays on the suppression list so
            they are never emailed again. Counts in campaign results are kept.
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-4 border-red-200 text-red-600 hover:border-red-300 hover:bg-red-50 hover:text-red-700"
            onClick={() => {
              setEraseError(null);
              setConfirmErase(true);
            }}
          >
            <Eraser className="h-4 w-4" aria-hidden="true" />
            Erase data…
          </Button>
        </section>
      ) : null}

      <TypeToConfirmDialog
        open={confirmErase}
        title={`Erase ${lead.email}?`}
        description="This can't be undone. Everything personal about this person is erased in every campaign, list and conversation."
        phrase={lead.email}
        confirmLabel="Erase data"
        loading={eraseMutation.isPending}
        error={eraseError}
        onConfirm={() => eraseMutation.mutate(lead.email)}
        onCancel={() => setConfirmErase(false)}
      />

      {activeWorkspaceId ? (
        <>
          <LeadCampaignHistory workspaceId={activeWorkspaceId} leadId={lead.id} />
          <LeadActivityTimeline workspaceId={activeWorkspaceId} leadId={lead.id} />
        </>
      ) : null}
    </main>
  );
}
