import { Suspense } from "react";

import { SmtpConnectClient } from "./smtp-connect-client";

export default function ConnectSmtpPage() {
  return (
    <Suspense fallback={null}>
      <SmtpConnectClient />
    </Suspense>
  );
}
