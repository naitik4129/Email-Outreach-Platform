import { Suspense } from "react";

import { LeadListDetailClient } from "./lead-list-detail-client";

export default async function LeadListDetailPage({
  params,
}: {
  params: Promise<{ list_id: string }>;
}) {
  const { list_id: listId } = await params;
  return (
    <Suspense fallback={null}>
      <LeadListDetailClient listId={listId} />
    </Suspense>
  );
}
