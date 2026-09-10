"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, KeyRound, Loader2 } from "lucide-react";
import { z } from "zod";

import { AuthCard } from "@/app/auth/auth-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/client";

const schema = z.object({
  password: z.string().min(8, "Password must be at least 8 characters"),
});

type FormValues = z.infer<typeof schema>;

type SessionState = "checking" | "valid" | "invalid" | "success";

export default function ResetPasswordPage() {
  const router = useRouter();
  const [state, setState] = useState<SessionState>("checking");
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(({ data }) => {
      setState(data.session ? "valid" : "invalid");
    });
  }, []);

  async function onSubmit(values: FormValues) {
    setFormError(null);
    const supabase = createClient();
    const { error } = await supabase.auth.updateUser({ password: values.password });
    if (error) {
      setFormError("This link may have expired. Request a new one and try again.");
      return;
    }
    setState("success");
    setTimeout(() => router.push("/auth/login"), 1500);
  }

  if (state === "checking") {
    return (
      <AuthCard icon={KeyRound} title="Reset your password">
        <div className="flex justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" aria-hidden="true" />
        </div>
      </AuthCard>
    );
  }

  if (state === "invalid") {
    return (
      <AuthCard icon={KeyRound} title="Link expired">
        <Alert>
          This password reset link is invalid or has already been used.
        </Alert>
        <p className="mt-6 text-center text-sm text-slate-500">
          <Link
            href="/auth/forgot-password"
            className="font-medium text-teal-700 hover:underline"
          >
            Request a new link
          </Link>
        </p>
      </AuthCard>
    );
  }

  if (state === "success") {
    return (
      <AuthCard icon={KeyRound} title="Password updated">
        <Alert variant="success">
          Your password has been updated. Redirecting to sign in&hellip;
        </Alert>
      </AuthCard>
    );
  }

  return (
    <AuthCard icon={KeyRound} title="Set a new password">
      <form className="space-y-4" onSubmit={handleSubmit(onSubmit)} noValidate>
        {formError ? <Alert>{formError}</Alert> : null}
        <Field id="password" label="New password" error={errors.password?.message}>
          <Input
            type="password"
            autoComplete="new-password"
            disabled={isSubmitting}
            {...register("password")}
          />
        </Field>
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <>
              Update password
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </>
          )}
        </Button>
      </form>
    </AuthCard>
  );
}
