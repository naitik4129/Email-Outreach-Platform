"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  Eye,
  Loader2,
  MailCheck,
  MessageSquare,
  Send,
} from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { getLeadActivity, type LeadActivityKind } from "@/lib/leads-api";

const KIND_PRESENTATION: Record<
  LeadActivityKind,
  {
    label: string;
    icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;
    tone: string;
  }
> = {
  EMAIL_SENT: { label: "Email Sent", icon: Send, tone: "bg-slate-100 text-slate-600" },
  EMAIL_OPENED: { label: "Email Opened", icon: Eye, tone: "bg-indigo-50 text-indigo-700" },
  EMAIL_BOUNCED: { label: "Email Bounced", icon: AlertOctagon, tone: "bg-rose-50 text-rose-700" },
  REPLY_RECEIVED: {
    label: "Reply Received",
    icon: MessageSquare,
    tone: "bg-emerald-50 text-emerald-700",
  },
  AUTO_REPLY_RECEIVED: {
    label: "Auto-reply Received",
    icon: MailCheck,
    tone: "bg-amber-50 text-amber-700",
  },
};

export function LeadActivityTimeline({
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

  return (
    <section className="rounded-md border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold tracking-normal text-slate-950">Activity</h2>
      {query.isLoading ? (
        <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading activity
        </div>
      ) : query.isError ? (
        <Alert variant="error" className="mt-3">
          We couldn&apos;t load this lead&apos;s activity.
        </Alert>
      ) : !query.data || query.data.items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          No emails have been sent to this lead yet.
        </p>
      ) : (
        <ol className="mt-4 space-y-4">
          {query.data.items.map((item) => {
            const presentation = KIND_PRESENTATION[item.kind];
            const Icon = presentation.icon;
            const context = [
              item.campaign_name,
              item.sequence_step_position ? `Step ${item.sequence_step_position}` : null,
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <li
                key={`${item.kind}-${item.message_id}`}
                className="flex gap-3"
                data-testid="lead-activity-item"
              >
                <span
                  className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full ${presentation.tone}`}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1 text-sm">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                    <p className="font-medium text-slate-950">
                      {presentation.label}
                      {item.kind === "EMAIL_OPENED" && (item.occurrence_count ?? 0) > 1
                        ? ` (${item.occurrence_count} times)`
                        : ""}
                      {item.kind === "EMAIL_BOUNCED" && item.bounce_type
                        ? ` (${item.bounce_type.toLowerCase()})`
                        : ""}
                    </p>
                    <time
                      className="text-xs text-slate-500"
                      dateTime={item.occurred_at}
                      title={new Date(item.occurred_at).toISOString()}
                    >
                      {new Date(item.occurred_at).toLocaleString()}
                    </time>
                  </div>
                  {context ? <p className="text-xs text-slate-500">{context}</p> : null}
                  {item.subject ? (
                    <p className="mt-0.5 truncate text-slate-700">{item.subject}</p>
                  ) : null}
                  {item.kind === "REPLY_RECEIVED" || item.kind === "AUTO_REPLY_RECEIVED" ? (
                    <div className="mt-1 rounded-md border border-slate-100 bg-slate-50 p-2 text-xs text-slate-700">
                      {item.sender_email ? (
                        <p className="font-medium text-slate-900">From {item.sender_email}</p>
                      ) : null}
                      {item.body_preview ? (
                        <p className="mt-0.5 whitespace-pre-line">{item.body_preview}</p>
                      ) : null}
                      <Link
                        href="/app/inbox"
                        className="mt-1 inline-block font-medium text-indigo-700 hover:underline"
                      >
                        Open in inbox
                      </Link>
                    </div>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
