import Link from "next/link";
import { ArrowRight, LockKeyhole } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function AuthPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4">
      <section className="w-full max-w-sm rounded-md border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-50 text-teal-700">
          <LockKeyhole className="h-5 w-5" aria-hidden="true" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-normal text-slate-950">
          Sign in
        </h1>
        <div className="mt-6 space-y-3">
          <div className="h-10 rounded-md border border-slate-200 bg-slate-50" />
          <div className="h-10 rounded-md border border-slate-200 bg-slate-50" />
          <Button asChild className="w-full">
            <Link href="/onboarding">
              Continue
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </Button>
        </div>
      </section>
    </main>
  );
}

