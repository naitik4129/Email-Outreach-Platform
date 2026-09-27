import { Activity, Database, Server, Waypoints } from "lucide-react";

import { PageHeader } from "@/components/ui/page-header";

import { BackendStatus } from "./backend-status";
import { DashboardOverview } from "./dashboard-overview";
import { WorkspaceSettings } from "./workspace-settings";

const systemItems = [
  { label: "API", icon: Server, value: "FastAPI boundary" },
  { label: "Database", icon: Database, value: "PostgreSQL readiness" },
  { label: "Queue", icon: Waypoints, value: "Redis broker" },
  { label: "Worker", icon: Activity, value: "Smoke task" },
];

export default function DashboardPage() {
  return (
    <main className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="Your outreach at a glance for the last 30 days."
        actions={<BackendStatus />}
      />

      <DashboardOverview />

      <section aria-labelledby="workspace-heading" className="space-y-4">
        <h2 id="workspace-heading" className="text-base font-semibold text-slate-900">
          Workspace
        </h2>
        <WorkspaceSettings />
      </section>

      <section aria-labelledby="system-heading" className="space-y-3">
        <h2 id="system-heading" className="text-base font-semibold text-slate-900">
          System
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {systemItems.map((item) => {
            const Icon = item.icon;
            return (
              <div
                key={item.label}
                className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3.5 shadow-card"
              >
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-slate-500">{item.label}</p>
                  <p className="truncate text-sm font-semibold text-slate-900">{item.value}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </main>
  );
}
