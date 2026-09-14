import { Server } from "lucide-react";

type Provider = "GMAIL" | "MICROSOFT" | "SMTP";

export function ProviderBadge({ provider }: { provider: Provider }) {
  if (provider === "MICROSOFT") {
    return (
      <span className="inline-flex items-center gap-1.5 font-semibold text-slate-900">
        <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
          <path fill="#F25022" d="M1 1h10v10H1z" />
          <path fill="#00A4EF" d="M1 13h10v10H1z" />
          <path fill="#7FBA00" d="M13 1h10v10H13z" />
          <path fill="#FFB900" d="M13 13h10v10H13z" />
        </svg>
        Microsoft 365
      </span>
    );
  }

  if (provider === "SMTP") {
    return (
      <span className="inline-flex items-center gap-1.5 font-semibold text-slate-900">
        <Server className="h-4 w-4 text-slate-500" aria-hidden="true" />
        Custom SMTP
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 font-semibold text-slate-900">
      <span className="text-rose-600">G</span>
      Gmail
    </span>
  );
}

export function providerSubtext(provider: Provider): string {
  switch (provider) {
    case "MICROSOFT":
      return "Microsoft Graph OAuth 2.0 PKCE envelope";
    case "SMTP":
      return "SMTP AUTH with STARTTLS/implicit TLS";
    default:
      return "OAuth 2.0 PKCE envelope";
  }
}
