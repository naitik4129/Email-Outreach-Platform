import { Suspense } from "react";
import { NewImportPageClient } from "./new-import-page-client";

export default function NewImportPage() {
  return (
    <Suspense fallback={null}>
      <NewImportPageClient />
    </Suspense>
  );
}
