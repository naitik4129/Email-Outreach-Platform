import { Suspense } from "react";

import { LeadDetailClient } from "./lead-detail-client";

export default async function LeadDetailPage({
  params,
}: {
  params: Promise<{ lead_id: string }>;
}) {
  const { lead_id: leadId } = await params;
  return (
    <Suspense fallback={null}>
      <LeadDetailClient leadId={leadId} />
    </Suspense>
  );
}
