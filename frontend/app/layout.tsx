import type { Metadata } from "next";

import { QueryProvider } from "@/lib/query-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Outly", template: "%s · Outly" },
  description: "Outly: run cold email outreach campaigns, manage leads and mailboxes, and turn conversations into opportunities.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}

