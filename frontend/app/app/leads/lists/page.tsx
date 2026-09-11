import { Suspense } from "react";

import { LeadListsPageClient } from "./lead-lists-page-client";

export default function LeadListsPage() {
  return (
    <Suspense fallback={null}>
      <LeadListsPageClient />
    </Suspense>
  );
}
