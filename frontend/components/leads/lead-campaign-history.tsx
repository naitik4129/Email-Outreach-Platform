"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Megaphone } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { StatusBadge } from "@/components/ui/status-badge";
import { getLeadActivity, type LeadActivityItem } from "@/lib/leads-api";

type CampaignRollup = {
  campaignId: string;
  campaignName: string;
  sent: number;
  opened: number;
  bounced: number;
  replied: number;
  lastActivityAt: string;
};

// Highest-priority outcome first, matching how the brief's example reads a
// lead's campaign history ("Sent" / "Opened" / "Replied") at a glance.
function headlineStatus(rollup: CampaignRollup): { label: string; tone: "danger" | "success" | "brand" | "neutral" } {
  if (rollup.replied > 0) return { label: "Replied", tone: "success" };
  if (rollup.bounced > 0) return { label: "Bounced", tone: "danger" };
  if (rollup.opened > 0) return { label: "Opened", tone: "brand" };
  return { label: "Sent", tone: "neutral" };
}

// Pure client-side re-grouping of the same activity feed LeadActivityTimeline
// already fetches (same query key, so react-query serves both from one
// request) -- no new backend call.
function rollupByCampaign(items: LeadActivityItem[]): CampaignRollup[] {
  const byCampaign = new Map<string, CampaignRollup>();
  for (const item of items) {
    if (!item.campaign_id) continue;
    let rollup = byCampaign.get(item.campaign_id);
    if (!rollup) {
      rollup = {
        campaignId: item.campaign_id,
        campaignName: item.campaign_name ?? "Untitled campaign",
        sent: 0,
        opened: 0,
        bounced: 0,
        replied: 0,
        lastActivityAt: item.occurred_at,
      };
      byCampaign.set(item.campaign_id, rollup);
    }
    if (item.kind === "EMAIL_SENT") rollup.sent += 1;
    else if (item.kind === "EMAIL_OPENED") rollup.opened += 1;
    else if (item.kind === "EMAIL_BOUNCED") rollup.bounced += 1;
    else if (item.kind === "REPLY_RECEIVED") rollup.replied += 1;
    // AUTO_REPLY_RECEIVED intentionally excluded from Replied, matching the
    // rest of the product (out-of-office replies don't count as engagement).
    if (item.occurred_at > rollup.lastActivityAt) rollup.lastActivityAt = item.occurred_at;
  }
  return [...byCampaign.values()].sort((a, b) => (a.lastActivityAt < b.lastActivityAt ? 1 : -1));
}

export function LeadCampaignHistory({
  workspaceId,
  leadId,
}: {
  workspaceId: string;
  leadId: string;
}) {
  const query = useQuery({
    queryKey: ["workspace", workspaceId, "leads", leadId, "activity"],
    queryFn: () => getLeadActivity(workspaceId, leadId),
  });

  const rollups = query.data ? rollupByCampaign(query.data.items) : [];

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <h2 className="text-lg font-semibold tracking-normal text-slate-900">Campaigns</h2>
      {query.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading campaign history
        </div>
      ) : query.isError ? (
        <Alert variant="error" className="mt-3">
          We couldn&apos;t load this lead&apos;s campaign history.
        </Alert>
      ) : rollups.length === 0 ? (
        <div className="mt-3 flex items-start gap-2 text-sm text-slate-500">
          <Megaphone className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          This lead hasn&apos;t been part of any campaign yet.
        </div>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100">
          {rollups.map((rollup) => {
            const status = headlineStatus(rollup);
            return (
              <li
                key={rollup.campaignId}
                className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
              >
                <Link
                  href={`/app/campaigns/${rollup.campaignId}`}
                  className="min-w-0 truncate text-sm font-medium text-slate-900 hover:underline"
                >
                  {rollup.campaignName}
                </Link>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="text-xs text-slate-500">
                    {rollup.sent} sent
                    {rollup.opened > 0 ? ` · ${rollup.opened} opened` : ""}
                    {rollup.bounced > 0 ? ` · ${rollup.bounced} bounced` : ""}
                    {rollup.replied > 0 ? ` · ${rollup.replied} replied` : ""}
                  </span>
                  <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
