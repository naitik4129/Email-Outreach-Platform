import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Email Outreach Platform",
  description: "Production application foundation for the email outreach platform.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

