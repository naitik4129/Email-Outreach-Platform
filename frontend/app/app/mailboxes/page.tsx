import { Suspense } from "react";

import { MailboxesPageClient } from "./mailboxes-page-client";

export default function MailboxesPage() {
  return (
    <Suspense fallback={null}>
      <MailboxesPageClient />
    </Suspense>
  );
}
