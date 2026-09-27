import * as React from "react";

import { cn } from "@/lib/utils";

// Inline SVG so the brand needs no asset pipeline. To use an official logo
// file later, replace the body of LogoMark only; every call site keeps working.
export function LogoMark({ className }: { className?: string }) {
  const gradientId = React.useId();
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn("h-8 w-8 shrink-0", className)}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1="4"
          y1="28"
          x2="28"
          y2="4"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#2563eb" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <circle
        cx="15"
        cy="17"
        r="9"
        fill="none"
        stroke={`url(#${gradientId})`}
        strokeWidth="5"
      />
      <path d="M22.6 4.4 27.5 2.5 25.6 7.4 24.4 6.2Z" fill="#7c3aed" />
      <circle cx="15" cy="17" r="3" fill="#ffffff" />
    </svg>
  );
}

export function Logo({
  className,
  showWordmark = true,
  size = "md",
  tone = "dark",
}: {
  className?: string;
  showWordmark?: boolean;
  size?: "sm" | "md" | "lg";
  // "light" is for use on dark brand surfaces.
  tone?: "dark" | "light";
}) {
  const mark = size === "lg" ? "h-10 w-10" : size === "sm" ? "h-6 w-6" : "h-8 w-8";
  const text = size === "lg" ? "text-3xl" : size === "sm" ? "text-lg" : "text-2xl";
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark className={mark} />
      {showWordmark ? (
        <span
          className={cn(
            "font-bold leading-none tracking-tight",
            tone === "light" ? "text-white" : "text-slate-900",
            text,
          )}
        >
          outly
        </span>
      ) : (
        <span className="sr-only">Outly</span>
      )}
    </span>
  );
}
