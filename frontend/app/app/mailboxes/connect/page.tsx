import { Suspense } from "react";

import { ConnectPageClient } from "./connect-page-client";

export default function ConnectMailboxPage() {
  return (
    <Suspense fallback={null}>
      <ConnectPageClient />
    </Suspense>
  );
}
