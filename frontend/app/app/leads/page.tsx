import { Suspense } from "react";

import { LeadsPageClient } from "./leads-page-client";

export default function LeadsPage() {
  return (
    <Suspense fallback={null}>
      <LeadsPageClient />
    </Suspense>
  );
}
