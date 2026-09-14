"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Mail,
  Plus,
  ShieldAlert,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { listMailboxes } from "@/lib/mailboxes-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { MailboxListItem } from "@/types/domain";

function canManageMailboxes(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function StatusBadge({ mailbox }: { mailbox: MailboxListItem }) {
  if (mailbox.connection_state === "DISCONNECTED") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
        <span className="h-1.5 w-1.5 rounded-full bg-slate-400" />
        Disconnected
      </span>
    );
  }

  if (
    mailbox.connection_state === "RECONNECT_REQUIRED" ||
    mailbox.health_state === "DEGRADED"
  ) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-700 border border-amber-200">
        <AlertTriangle className="h-3 w-3" />
        Needs Reconnect
      </span>
    );
  }

  if (mailbox.connection_state === "CONNECTED" && mailbox.health_state === "HEALTHY") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700 border border-emerald-200">
        <CheckCircle2 className="h-3 w-3" />
        Connected
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600">
      {mailbox.connection_state}
    </span>
  );
}

export function MailboxesPageClient() {
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayManage = canManageMailboxes(activeWorkspace?.role_code);

  const mailboxesQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "mailboxes"],
    queryFn: () =>
      activeWorkspaceId
        ? listMailboxes(activeWorkspaceId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId),
  });

  const mailboxes = mailboxesQuery.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">
            Mailboxes
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Connect and manage sending email accounts for your campaigns and outreach.
          </p>
        </div>
        {mayManage ? (
          <Button asChild>
            <Link href="/app/mailboxes/connect">
              <Plus className="h-4 w-4" aria-hidden="true" />
              Connect Mailbox
            </Link>
          </Button>
        ) : null}
      </div>

      {mailboxesQuery.error ? (
        <Alert variant="error">
          {mailboxesQuery.error instanceof ApiError
            ? mailboxesQuery.error.message
            : "Failed to load mailboxes. Please try again."}
        </Alert>
      ) : null}

      {mailboxesQuery.isLoading ? (
        <div className="flex min-h-[300px] items-center justify-center rounded-xl border border-slate-200 bg-white p-12">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      ) : mailboxes.length === 0 ? (
        <div className="flex min-h-[360px] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center">
          <div className="rounded-full bg-slate-100 p-3 text-slate-600">
            <Mail className="h-6 w-6" aria-hidden="true" />
          </div>
          <h3 className="mt-4 text-base font-semibold text-slate-900">
            No mailboxes connected yet
          </h3>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            Connect your Gmail account to begin sending authenticated outreach with zero credential exposure.
          </p>
          {mayManage ? (
            <div className="mt-6">
              <Button asChild>
                <Link href="/app/mailboxes/connect">
                  <Plus className="h-4 w-4" aria-hidden="true" />
                  Connect Mailbox
                </Link>
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs font-semibold uppercase tracking-wider text-slate-500">
                <tr>
                  <th scope="col" className="px-6 py-3.5">
                    Account
                  </th>
                  <th scope="col" className="px-6 py-3.5">
                    Provider
                  </th>
                  <th scope="col" className="px-6 py-3.5">
                    Status
                  </th>
                  <th scope="col" className="px-6 py-3.5">
                    Policy
                  </th>
                  <th scope="col" className="px-6 py-3.5">
                    Connected At
                  </th>
                  <th scope="col" className="px-6 py-3.5 text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {mailboxes.map((mailbox) => (
                  <tr
                    key={mailbox.id}
                    className="hover:bg-slate-50/75 transition-colors"
                  >
                    <td className="px-6 py-4">
                      <div className="font-medium text-slate-900">
                        {mailbox.email_address}
                      </div>
                      {mailbox.sender_display_name ? (
                        <div className="text-xs text-slate-500">
                          {mailbox.sender_display_name}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-800">
                        <span className="font-semibold text-rose-600">G</span>
                        {mailbox.provider === "GMAIL" ? "Gmail" : mailbox.provider}
                      </span>
                    </td>
                    <td className="px-6 py-4">
                      <StatusBadge mailbox={mailbox} />
                    </td>
                    <td className="px-6 py-4">
                      {mailbox.policy_state === "ENABLED" ? (
                        <span className="text-xs text-slate-600">Normal</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-600">
                          <ShieldAlert className="h-3.5 w-3.5" />
                          {mailbox.policy_reason || "Restricted"}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-xs text-slate-500">
                      {formatDate(mailbox.created_at)}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <Button variant="outline" size="sm" asChild>
                        <Link href={`/app/mailboxes/${mailbox.id}`}>
                          Manage
                        </Link>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
