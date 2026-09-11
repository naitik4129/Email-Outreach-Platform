import { Suspense } from "react";
import { ImportDetailPageClient } from "./import-detail-page-client";

export default async function ImportDetailPage({
  params,
}: {
  params: Promise<{ import_id: string }>;
}) {
  const resolvedParams = await params;
  return (
    <Suspense fallback={null}>
      <ImportDetailPageClient importId={resolvedParams.import_id} />
    </Suspense>
  );
}

