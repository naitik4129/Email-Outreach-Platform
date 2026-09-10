"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Building2, Loader2 } from "lucide-react";
import { z } from "zod";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createWorkspace, listWorkspaces } from "@/lib/workspaces-api";

const schema = z.object({
  name: z.string().min(1, "Workspace name is required").max(200),
});

type FormValues = z.infer<typeof schema>;

const STORAGE_KEY = "active-workspace-id";

export default function WorkspaceOnboardingPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  // Stable across retries of the *same* submission attempt, so a network
  // retry or an accidental double submit reuses one idempotency key instead
  // of creating two workspaces.
  const idempotencyKeyRef = useRef<string>(crypto.randomUUID());
  const submittingRef = useRef(false);

  const { data: existingWorkspaces, isLoading } = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
  });

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  const alreadyHasWorkspace = Boolean(
    !isLoading && existingWorkspaces && existingWorkspaces.length > 0,
  );

  useEffect(() => {
    if (alreadyHasWorkspace) {
      router.replace("/app/dashboard");
    }
  }, [alreadyHasWorkspace, router]);

  if (alreadyHasWorkspace) {
    return null;
  }

  async function onSubmit(values: FormValues) {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setFormError(null);
    try {
      const workspace = await createWorkspace(values.name, idempotencyKeyRef.current);
      try {
        window.localStorage.setItem(STORAGE_KEY, workspace.id);
      } catch {
        // Non-fatal: /app will just fall back to the first workspace it sees.
      }
      // The dashboard's WorkspaceProvider reads the same ["workspaces"]
      // query key; without invalidating it here, it would see this page's
      // still-cached empty result and immediately bounce back to onboarding.
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      router.push("/app/dashboard");
    } catch (err) {
      idempotencyKeyRef.current = crypto.randomUUID();
      setFormError(
        err instanceof ApiError
          ? "We couldn't create your workspace. Please try again."
          : "Network error. Please check your connection and try again.",
      );
      submittingRef.current = false;
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4">
      <section className="w-full max-w-xl rounded-md border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-50 text-teal-700">
          <Building2 className="h-5 w-5" aria-hidden="true" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-normal text-slate-950">
          Create your workspace
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          A workspace is where your team, leads, and campaigns will live.
        </p>
        <form
          className="mt-6 space-y-4"
          onSubmit={handleSubmit(onSubmit)}
          noValidate
        >
          {formError ? <Alert>{formError}</Alert> : null}
          <Field id="name" label="Workspace name" error={errors.name?.message}>
            <Input
              placeholder="Acme Outreach"
              disabled={isSubmitting}
              {...register("name")}
            />
          </Field>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <>
                Create workspace
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </>
            )}
          </Button>
        </form>
      </section>
    </main>
  );
}
