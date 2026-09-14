import { Suspense } from "react";

import { CampaignsPageClient } from "./campaigns-page-client";

export default function CampaignsPage() {
  return (
    <Suspense fallback={null}>
      <CampaignsPageClient />
    </Suspense>
  );
}
