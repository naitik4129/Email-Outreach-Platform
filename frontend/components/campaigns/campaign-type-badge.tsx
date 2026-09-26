import { Sparkles } from "lucide-react";

import type { CampaignType } from "@/types/domain";

// Shown only for hyper-personalized campaigns; standard campaigns stay unmarked.
export function CampaignTypeBadge({ type }: { type: CampaignType | undefined }) {
  if (type !== "HYPER_PERSONALIZED") return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2.5 py-0.5 text-xs font-medium text-violet-700">
      <Sparkles className="h-3 w-3" aria-hidden="true" />
      Hyper-Personalized
    </span>
  );
}
