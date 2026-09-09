import { Activity, Database, Server, Waypoints } from "lucide-react";

import { BackendStatus } from "./backend-status";

const foundationItems = [
  { label: "FastAPI", icon: Server, value: "API boundary" },
  { label: "PostgreSQL", icon: Database, value: "Readiness check" },
  { label: "Redis", icon: Waypoints, value: "Queue broker" },
  { label: "Worker", icon: Activity, value: "Smoke task" },
];

export default function DashboardPage() {
  return (
    <main className="space-y-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-teal-700">Phase 0</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal text-slate-950">
            Dashboard
          </h1>
        </div>
        <BackendStatus />
      </div>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {foundationItems.map((item) => {
          const Icon = item.icon;
          return (
            <div
              key={item.label}
              className="rounded-md border border-slate-200 bg-white p-4 shadow-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-slate-500">{item.label}</p>
                  <p className="mt-2 text-base font-semibold text-slate-950">
                    {item.value}
                  </p>
                </div>
                <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-50 text-teal-700">
                  <Icon className="h-5 w-5" aria-hidden="true" />
                </div>
              </div>
            </div>
          );
        })}
      </section>

      <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
        <div className="max-w-3xl">
          <h2 className="text-lg font-semibold tracking-normal text-slate-950">
            Application Foundation
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            The authenticated workspace surface is ready for Phase 1 integration.
          </p>
        </div>
      </section>
    </main>
  );
}

