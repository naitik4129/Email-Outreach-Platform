"use client";

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, getBackendHealth } from "@/lib/api-client";

type Status =
  | { state: "loading" }
  | { state: "ok"; requestId: string | null }
  | { state: "error"; message: string };

export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ state: "loading" });

  useEffect(() => {
    let active = true;
    getBackendHealth()
      .then((result) => {
        if (active) {
          setStatus({ state: "ok", requestId: result.requestId });
        }
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        const message =
          error instanceof ApiError
            ? error.message
            : "Backend is not reachable from the browser.";
        setStatus({ state: "error", message });
      });
    return () => {
      active = false;
    };
  }, []);

  if (status.state === "loading") {
    return (
      <div className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Checking backend
      </div>
    );
  }

  if (status.state === "ok") {
    return (
      <div className="inline-flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
        Backend online
      </div>
    );
  }

  return (
    <div className="inline-flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <AlertCircle className="h-4 w-4" aria-hidden="true" />
      {status.message}
    </div>
  );
}

