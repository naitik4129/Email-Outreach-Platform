import { Suspense } from "react";
import { SuppressionPageClient } from "./suppression-page-client";

export default function SuppressionPage() {
  return (
    <Suspense fallback={null}>
      <SuppressionPageClient />
    </Suspense>
  );
}
