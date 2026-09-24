import { AppShell } from "@/app/app/app-shell";
import { QueryProvider } from "@/lib/query-provider";

// The root layout already provides a QueryClient, but the Linux/Docker
// production build (Next 15.5.25, `next build` in frontend/Dockerfile) failed
// prerendering with "No QueryClient set" until a provider was also scoped to
// the authenticated app. The same build passes on Windows, and the cause has
// not been identified; this keeps the /app/* tree independent of it.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <QueryProvider>
      <AppShell>{children}</AppShell>
    </QueryProvider>
  );
}
