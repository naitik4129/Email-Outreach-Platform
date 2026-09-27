import * as React from "react";
import {
  AlertCircle,
  Archive,
  CheckCircle2,
  Clock,
  Pause,
  PencilLine,
  Play,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { CampaignStatus } from "@/types/domain";

export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "muted";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-500/15",
  brand: "bg-brand-50 text-brand-700 ring-brand-600/20",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  warning: "bg-amber-50 text-amber-800 ring-amber-600/25",
  danger: "bg-red-50 text-red-700 ring-red-600/20",
  muted: "bg-slate-50 text-slate-500 ring-slate-400/20",
};

// Generic pill. Always carries text (and usually an icon) so status is never
// conveyed by color alone.
export function StatusBadge({
  tone = "neutral",
  icon,
  children,
  className,
}: {
  tone?: Tone;
  icon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset [&>svg]:h-3 [&>svg]:w-3 [&>svg]:shrink-0",
        toneClasses[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

type CampaignStatusMeta = { tone: Tone; icon: React.ReactNode; accent: string };

// `accent` is the restrained top-edge color used on campaign cards.
export const campaignStatusMeta: Record<CampaignStatus, CampaignStatusMeta> = {
  DRAFT: { tone: "neutral", icon: <PencilLine aria-hidden="true" />, accent: "bg-slate-300" },
  SCHEDULED: { tone: "brand", icon: <Clock aria-hidden="true" />, accent: "bg-brand-500" },
  RUNNING: { tone: "success", icon: <Play aria-hidden="true" />, accent: "bg-emerald-500" },
  PAUSED: { tone: "warning", icon: <Pause aria-hidden="true" />, accent: "bg-amber-400" },
  ERROR: { tone: "danger", icon: <AlertCircle aria-hidden="true" />, accent: "bg-red-500" },
  COMPLETED: { tone: "brand", icon: <CheckCircle2 aria-hidden="true" />, accent: "bg-brand-400" },
  ARCHIVED: { tone: "muted", icon: <Archive aria-hidden="true" />, accent: "bg-slate-200" },
};

// Renders the raw status code as text.
export function CampaignStatusBadge({
  status,
  className,
}: {
  status: CampaignStatus;
  className?: string;
}) {
  // Tolerate a status code this build doesn't know about yet.
  const meta = campaignStatusMeta[status] ?? { tone: "neutral" as const, icon: null };
  return (
    <StatusBadge tone={meta.tone} icon={meta.icon} className={className}>
      {status}
    </StatusBadge>
  );
}

// Background import job states (list and detail pages share the mapping).
export function ImportStatusBadge({ status }: { status: string }) {
  const tone: Tone =
    status === "COMPLETED"
      ? "success"
      : status === "FAILED"
        ? "danger"
        : status === "COMPLETED_WITH_ERRORS"
          ? "warning"
          : "brand";
  return <StatusBadge tone={tone}>{status}</StatusBadge>;
}
