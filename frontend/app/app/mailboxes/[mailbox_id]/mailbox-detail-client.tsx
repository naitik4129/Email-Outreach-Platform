"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Send,
  Shield,
  Trash2,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  disconnectMailbox,
  getMailbox,
  reconnectGmail,
  sendControlledTestEmail,
  updateMailbox,
} from "@/lib/mailboxes-api";
import { useWorkspace } from "@/lib/workspace-context";
import type { MailboxTestSendResult } from "@/types/domain";

function canManageMailboxes(role?: string) {
  return role === "OWNER" || role === "ADMIN" || role === "MANAGER";
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function MailboxDetailClient({ mailboxId: propId }: { mailboxId?: string } = {}) {
  const params = useParams<{ mailbox_id: string }>();
  const mailboxId = propId || params.mailbox_id;
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayManage = canManageMailboxes(activeWorkspace?.role_code);

  const isNewlyConnected = searchParams.get("connected") === "true";

  // Form states
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [signatureHtml, setSignatureHtml] = useState<string | null>(null);
  const [hasLoadedForm, setHasLoadedForm] = useState(false);

  // Test send states
  const [testRecipient, setTestRecipient] = useState("");
  const [testSendResult, setTestSendResult] = useState<MailboxTestSendResult | null>(null);
  const [testSendError, setTestSendError] = useState<string | null>(null);

  // Disconnect confirmation
  const [showDisconnectConfirm, setShowDisconnectConfirm] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);

  const mailboxQuery = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "mailbox", mailboxId],
    queryFn: async () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      const data = await getMailbox(activeWorkspaceId, mailboxId);
      if (!hasLoadedForm) {
        setDisplayName(data.sender_display_name);
        setSignatureHtml(data.signature_html);
        setTestRecipient(data.email_address);
        setHasLoadedForm(true);
      }
      return data;
    },
    enabled: Boolean(activeWorkspaceId && mailboxId),
  });

  const mailbox = mailboxQuery.data;

  // Mutations
  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return updateMailbox(activeWorkspaceId, mailboxId, {
        sender_display_name: displayName,
        signature_html: signatureHtml,
      });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(
        ["workspace", activeWorkspaceId, "mailbox", mailboxId],
        updated,
      );
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "mailboxes"],
      });
      setActionSuccess("Settings updated successfully.");
      setActionError(null);
    },
    onError: (err) => {
      setActionSuccess(null);
      setActionError(err instanceof ApiError ? err.message : "Failed to update mailbox.");
    },
  });

  const testSendMutation = useMutation({
    mutationFn: async () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      setTestSendError(null);
      setTestSendResult(null);
      return sendControlledTestEmail(activeWorkspaceId, mailboxId, testRecipient);
    },
    onSuccess: (result) => {
      setTestSendResult(result);
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "mailbox", mailboxId],
      });
    },
    onError: (err) => {
      setTestSendError(
        err instanceof ApiError
          ? err.message
          : "Failed to send test email. Please try again.",
      );
    },
  });

  const reconnectMutation = useMutation({
    mutationFn: async () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return reconnectGmail(activeWorkspaceId, mailboxId);
    },
    onSuccess: (res) => {
      window.location.href = res.authorization_url;
    },
    onError: (err) => {
      setActionError(
        err instanceof ApiError ? err.message : "Failed to initiate reconnection.",
      );
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: async () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return disconnectMailbox(activeWorkspaceId, mailboxId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "mailboxes"],
      });
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "mailbox", mailboxId],
      });
      setShowDisconnectConfirm(false);
      setActionSuccess("Mailbox disconnected and credentials zeroed.");
    },
    onError: (err) => {
      setActionError(
        err instanceof ApiError ? err.message : "Failed to disconnect mailbox.",
      );
    },
  });

  if (mailboxQuery.isLoading) {
    return (
      <div className="flex min-h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
      </div>
    );
  }

  if (mailboxQuery.error || !mailbox) {
    return (
      <div className="mx-auto max-w-xl space-y-4 py-8 text-center">
        <Alert variant="error">
          {mailboxQuery.error instanceof ApiError
            ? mailboxQuery.error.message
            : "Mailbox not found or unavailable."}
        </Alert>
        <Button variant="outline" asChild>
          <Link href="/app/mailboxes">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Return to Mailboxes
          </Link>
        </Button>
      </div>
    );
  }

  const isConnected = mailbox.connection_state === "CONNECTED";
  const isHealthy = mailbox.health_state === "HEALTHY";
  const needsReconnect =
    mailbox.connection_state === "RECONNECT_REQUIRED" ||
    mailbox.health_state === "DEGRADED";

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      {/* Back button & Header */}
      <div>
        <Button variant="ghost" size="sm" asChild className="-ml-3 mb-2 text-slate-500">
          <Link href="/app/mailboxes">
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            Back to Mailboxes
          </Link>
        </Button>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">
                {mailbox.email_address}
              </h1>
              <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-800">
                <span className="font-semibold text-rose-600">G</span> Gmail
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Connected generation {mailbox.current_connection_generation} · Added{" "}
              {formatDate(mailbox.created_at)}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {needsReconnect && mayManage ? (
              <Button
                onClick={() => reconnectMutation.mutate()}
                disabled={reconnectMutation.isPending}
                className="bg-amber-600 hover:bg-amber-700 text-white"
              >
                {reconnectMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Reconnect Google
              </Button>
            ) : null}
            {isConnected && mayManage && !showDisconnectConfirm ? (
              <Button
                variant="outline"
                className="text-red-600 border-red-200 hover:bg-red-50 hover:text-red-700"
                onClick={() => setShowDisconnectConfirm(true)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Disconnect
              </Button>
            ) : null}
          </div>
        </div>
      </div>

      {/* Newly connected alert */}
      {isNewlyConnected ? (
        <Alert variant="success">
          Your Gmail account has been securely authenticated and connected. You can now send a controlled test email below.
        </Alert>
      ) : null}

      {/* Action feedback */}
      {actionSuccess ? <Alert variant="success">{actionSuccess}</Alert> : null}
      {actionError ? <Alert variant="error">{actionError}</Alert> : null}

      {/* Disconnect confirmation card */}
      {showDisconnectConfirm ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6">
          <h3 className="text-base font-semibold text-red-900">
            Are you sure you want to disconnect this mailbox?
          </h3>
          <p className="mt-1 text-sm text-red-700">
            Disconnecting immediately zeroes all stored cryptographic tokens and revokes OAuth permissions with Google. Any active sending through this mailbox will stop.
          </p>
          <div className="mt-4 flex gap-3">
            <Button
              variant="danger"
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
            >
              {disconnectMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Confirm Disconnect
            </Button>
            <Button
              variant="outline"
              onClick={() => setShowDisconnectConfirm(false)}
              disabled={disconnectMutation.isPending}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {/* Status & Diagnostics grid */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Connection
            </span>
            {isConnected ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-slate-400" />
            )}
          </div>
          <p className="mt-2 text-base font-bold text-slate-900">
            {mailbox.connection_state}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            OAuth 2.0 PKCE envelope
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Health State
            </span>
            {isHealthy ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-amber-600" />
            )}
          </div>
          <p className="mt-2 text-base font-bold text-slate-900">
            {mailbox.health_state}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            Token freshness & validation
          </p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Policy & Safety
            </span>
            <Shield className="h-4 w-4 text-slate-400" />
          </div>
          <p className="mt-2 text-base font-bold text-slate-900">
            {mailbox.policy_state}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {mailbox.policy_reason || "No restrictions active"}
          </p>
        </div>
      </div>

      {/* Controlled Test Email Section */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-5">
        <div>
          <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <Send className="h-4 w-4 text-teal-600" />
            Send Controlled Test Email
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Validate the end-to-end delivery pipeline through Google API. The test email is bounded, checked against suppression rules, and recorded in the message log.
          </p>
        </div>

        {testSendResult ? (
          <Alert variant="success">
            Test email dispatched successfully! Provider message ID:{" "}
            <code className="rounded bg-emerald-100 px-1 py-0.5 text-xs font-mono">
              {testSendResult.provider_message_id}
            </code>
          </Alert>
        ) : null}

        {testSendError ? <Alert variant="error">{testSendError}</Alert> : null}

        <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
          <div className="flex-1">
            <Field id="test-recipient" label="Recipient Email Address" required>
              <Input
                type="email"
                value={testRecipient}
                onChange={(e) => setTestRecipient(e.target.value)}
                placeholder="test@example.com"
                disabled={!isConnected || testSendMutation.isPending || !mayManage}
              />
            </Field>
          </div>
          <Button
            onClick={() => testSendMutation.mutate()}
            disabled={
              !isConnected ||
              !testRecipient.trim() ||
              testSendMutation.isPending ||
              !mayManage
            }
            className="bg-teal-600 hover:bg-teal-700 text-white shrink-0"
          >
            {testSendMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Sending Test Email...
              </>
            ) : (
              <>
                <Send className="mr-2 h-4 w-4" />
                Send Test Email
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Sender Settings Form */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm space-y-6">
        <div>
          <h2 className="text-base font-bold text-slate-900">
            Sender Settings
          </h2>
          <p className="text-sm text-slate-500 mt-1">
            Configure the display name and email signature that recipients see on outbound messages.
          </p>
        </div>

        <div className="space-y-4">
          <Field id="display-name" label="Sender Display Name">
            <Input
              value={displayName ?? ""}
              onChange={(e) => setDisplayName(e.target.value || null)}
              placeholder="e.g. Alex Smith"
              disabled={!mayManage || updateMutation.isPending}
            />
          </Field>

          <Field id="signature" label="Email Signature (HTML / Text)">
            <textarea
              id="signature"
              rows={4}
              value={signatureHtml ?? ""}
              onChange={(e) => setSignatureHtml(e.target.value || null)}
              placeholder="<p>Best regards,<br/>Alex Smith</p>"
              disabled={!mayManage || updateMutation.isPending}
              className="w-full rounded-md border border-slate-200 bg-white p-3 text-sm text-slate-900 shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600 disabled:opacity-50 font-mono"
            />
          </Field>
        </div>

        {mayManage ? (
          <div className="flex justify-end pt-2">
            <Button
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Save Changes
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
