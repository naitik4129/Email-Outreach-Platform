"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Loader2, MailCheck, Users, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { AuthCard } from "@/app/auth/auth-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { acceptInvitation, verifyInvitation } from "@/lib/team-api";

function AcceptInviteContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const {
    data: invite,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["invitation", token],
    queryFn: () => verifyInvitation(token),
    enabled: Boolean(token),
    retry: false,
  });

  const acceptMutation = useMutation({
    mutationFn: () => acceptInvitation(token),
    onSuccess: (data) => {
      setSuccess(true);
      setErrorMessage(null);
      // Wait a moment then redirect to dashboard
      setTimeout(() => {
        router.push("/app/dashboard");
      }, 1500);
    },
    onError: (err: unknown) => {
      setErrorMessage(
        err instanceof ApiError
          ? err.message
          : "Failed to accept invitation. It may have expired.",
      );
    },
  });

  if (!token) {
    return (
      <AuthCard
        icon={XCircle}
        title="Invalid Link"
        subtitle="This invitation link is missing a verification token."
      >
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            Please check the link provided in your email or ask your team administrator to send a new invitation.
          </p>
          <Button asChild variant="outline" className="w-full">
            <Link href="/auth/login">Return to Sign In</Link>
          </Button>
        </div>
      </AuthCard>
    );
  }

  if (isLoading) {
    return (
      <AuthCard
        icon={Users}
        title="Verifying Invitation"
        subtitle="Please wait while we verify your workspace invitation..."
      >
        <div className="flex items-center justify-center gap-2 py-8 text-sm text-slate-500">
          <Loader2 className="h-5 w-5 animate-spin text-teal-600" />
          Checking token validity&hellip;
        </div>
      </AuthCard>
    );
  }

  if (isError || !invite) {
    return (
      <AuthCard
        icon={XCircle}
        title="Invitation Invalid"
        subtitle="We could not verify this invitation."
      >
        <div className="space-y-4">
          <Alert variant="error">
            {error instanceof ApiError
              ? error.message
              : "This invitation link is invalid or has expired."}
          </Alert>
          <Button asChild variant="outline" className="w-full">
            <Link href="/auth/login">Return to Sign In</Link>
          </Button>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      icon={MailCheck}
      title="Join Workspace"
      subtitle={`You've been invited to join ${invite.workspace_name}`}
      width="md"
    >
      <div className="space-y-5">
        {errorMessage && <Alert variant="error">{errorMessage}</Alert>}

        {success ? (
          <div className="space-y-3 py-2 text-center">
            <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-teal-50 text-teal-600">
              <CheckCircle2 className="h-6 w-6" />
            </div>
            <h3 className="text-base font-semibold text-slate-900">
              Welcome to {invite.workspace_name}!
            </h3>
            <p className="text-sm text-slate-600">
              Invitation accepted. Redirecting you to your dashboard&hellip;
            </p>
          </div>
        ) : (
          <>
            <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-4 space-y-2.5 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Workspace:</span>
                <span className="font-semibold text-slate-900">
                  {invite.workspace_name}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Invited Email:</span>
                <span className="font-medium text-slate-800">{invite.email}</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Assigned Role:</span>
                <span className="inline-flex rounded-full bg-teal-100 px-2.5 py-0.5 text-xs font-semibold text-teal-800">
                  {invite.role_code}
                </span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-500">Expires:</span>
                <span className="text-xs text-slate-600">
                  {new Date(invite.expires_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </div>
            </div>

            <Button
              type="button"
              variant="primary"
              className="w-full"
              disabled={acceptMutation.isPending}
              onClick={() => acceptMutation.mutate()}
            >
              {acceptMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  Accepting Invitation&hellip;
                </>
              ) : (
                "Accept Invitation & Join"
              )}
            </Button>

            <p className="text-center text-xs text-slate-500">
              Need to sign in with a different account?{" "}
              <Link href="/auth/login" className="text-teal-600 hover:underline">
                Sign in
              </Link>
            </p>
          </>
        )}
      </div>
    </AuthCard>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense
      fallback={
        <div className="grid min-h-screen place-items-center bg-slate-50">
          <Loader2 className="h-6 w-6 animate-spin text-slate-400" />
        </div>
      }
    >
      <AcceptInviteContent />
    </Suspense>
  );
}
