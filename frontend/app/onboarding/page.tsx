import Link from "next/link";
import { ArrowRight, Building2 } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function OnboardingPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4">
      <section className="w-full max-w-xl rounded-md border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-50 text-teal-700">
          <Building2 className="h-5 w-5" aria-hidden="true" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-normal text-slate-950">
          Onboarding
        </h1>
        <div className="mt-6 grid gap-3 sm:grid-cols-3">
          {["Workspace", "Mailbox", "Leads"].map((item) => (
            <div
              key={item}
              className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm font-medium text-slate-700"
            >
              {item}
            </div>
          ))}
        </div>
        <Button asChild className="mt-6">
          <Link href="/app/dashboard">
            Enter app
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
      </section>
    </main>
  );
}

