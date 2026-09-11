import { Suspense } from "react";
import { ImportsPageClient } from "./imports-page-client";

export default function ImportsPage() {
  return (
    <Suspense fallback={null}>
      <ImportsPageClient />
    </Suspense>
  );
}
