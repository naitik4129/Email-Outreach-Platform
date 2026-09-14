"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { completeGmailOAuth } from "@/lib/mailboxes-api";
import { useWorkspace } from "@/lib/workspace-context";

function GmailCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { activeWorkspaceId } = useWorkspace();

  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const urlError = searchParams.get("error");

  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const attemptedRef = useRef(false);

  useEffect(() => {
    if (attemptedRef.current) return;

    if (urlError) {
      attemptedRef.current = true;
      setStatus("error");
      setErrorMessage(`Google authentication error: ${urlError}`);
      return;
    }

    if (!code || !state) {
      attemptedRef.current = true;
      setStatus("error");
      setErrorMessage("Missing required OAuth parameters (code or state).");
      return;
    }

    if (!activeWorkspaceId) {
      // Wait until workspace context resolves
      return;
    }

    attemptedRef.current = true;

    async function finishOAuth() {
      try {
        const res = await completeGmailOAuth(activeWorkspaceId!, {
          code: code!,
          state: state!,
        });

        setStatus("success");
        await queryClient.invalidateQueries({
          queryKey: ["workspace", activeWorkspaceId, "mailboxes"],
        });

        // Navigate to mailbox detail after brief success message
        setTimeout(() => {
          router.push(`/app/mailboxes/${res.mailbox_id}?connected=true`);
        }, 1200);
      } catch (err) {
        setStatus("error");
        if (err instanceof ApiError) {
          setErrorMessage(err.message);
        } else {
          setErrorMessage("Failed to complete Google connection. Please try again.");
        }
      }
    }

    finishOAuth();
  }, [code, state, urlError, activeWorkspaceId, router, queryClient]);

  return (
    <div className="mx-auto flex min-h-[400px] max-w-md flex-col items-center justify-center p-6 text-center">
      {status === "loading" && (
        <div className="space-y-4">
          <Loader2 className="mx-auto h-10 w-10 animate-spin text-teal-600" />
          <h2 className="text-lg font-semibold text-slate-900">
            Completing Google Authentication
          </h2>
          <p className="text-sm text-slate-500">
            Exchanging authorization tokens and establishing secure encrypted credentials...
          </p>
        </div>
      )}

      {status === "success" && (
        <div className="space-y-4">
          <CheckCircle2 className="mx-auto h-12 w-12 text-emerald-600" />
          <h2 className="text-lg font-semibold text-slate-900">
            Mailbox Connected Successfully!
          </h2>
          <p className="text-sm text-slate-500">
            Redirecting to your mailbox management dashboard...
          </p>
        </div>
      )}

      {status === "error" && (
        <div className="w-full space-y-6">
          <XCircle className="mx-auto h-12 w-12 text-red-600" />
          <div>
            <h2 className="text-lg font-semibold text-slate-900">
              Connection Failed
            </h2>
            <div className="mt-4">
              <Alert variant="error">{errorMessage}</Alert>
            </div>
          </div>
          <div className="flex justify-center gap-3">
            <Button variant="outline" asChild>
              <Link href="/app/mailboxes">Return to Mailboxes</Link>
            </Button>
            <Button asChild>
              <Link href="/app/mailboxes/connect">Try Again</Link>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function GmailCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[400px] items-center justify-center">
          <Loader2 className="h-8 w-8 animate-spin text-slate-400" />
        </div>
      }
    >
      <GmailCallbackInner />
    </Suspense>
  );
}
