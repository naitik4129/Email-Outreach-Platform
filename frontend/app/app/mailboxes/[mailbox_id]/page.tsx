import { Suspense } from "react";

import { MailboxDetailClient } from "./mailbox-detail-client";

export default function MailboxDetailPage() {
  return (
    <Suspense fallback={null}>
      <MailboxDetailClient />
    </Suspense>
  );
}
