import { Suspense } from "react";

import { InboxPageClient } from "./inbox-page-client";

export default function InboxPage() {
  return (
    <Suspense fallback={null}>
      <InboxPageClient />
    </Suspense>
  );
}
