import { Suspense } from "react";

import { TemplatesPageClient } from "./templates-page-client";

export default function TemplatesPage() {
  return (
    <Suspense fallback={null}>
      <TemplatesPageClient />
    </Suspense>
  );
}
