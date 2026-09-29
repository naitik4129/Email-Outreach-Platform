import { PageHeader } from "@/components/ui/page-header";

import { DashboardOverview } from "./dashboard-overview";
import { WorkspaceSettings } from "./workspace-settings";

export default function DashboardPage() {
  return (
    <main className="space-y-8">
      <PageHeader
        title="Dashboard"
        description="Your outreach at a glance for the last 30 days."
      />

      <DashboardOverview />

      <section aria-labelledby="workspace-heading" className="space-y-4">
        <h2 id="workspace-heading" className="text-base font-semibold text-slate-900">
          Workspace
        </h2>
        <WorkspaceSettings />
      </section>
    </main>
  );
}
