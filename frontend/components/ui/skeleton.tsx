import { cn } from "@/lib/utils";

// Placeholder shape shown while real data loads. Purely visual: the loading
// container announces state via aria-busy, not the skeleton itself.
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-slate-200/80", className)}
    />
  );
}

export function SkeletonLines({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2.5", className)} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={cn("h-3.5", i === lines - 1 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

// Drop-in replacement for the old centered spinner. "lg" is a whole-page/card
// placeholder; "sm" sits inside an existing card or section.
export function LoadingBlock({
  size = "sm",
  className,
}: {
  size?: "sm" | "lg";
  className?: string;
}) {
  if (size === "sm") {
    return (
      <div aria-busy="true" className={cn("space-y-3 py-2", className)}>
        <Skeleton className="h-4 w-1/3" />
        <SkeletonLines lines={2} />
      </div>
    );
  }
  return (
    <div
      aria-busy="true"
      className={cn(
        "space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-card",
        className,
      )}
    >
      <Skeleton className="h-5 w-1/4" />
      <SkeletonLines lines={3} />
      <Skeleton className="h-28 w-full rounded-lg" />
    </div>
  );
}
