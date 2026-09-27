import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";

import { cn } from "@/lib/utils";

export function Alert({
  variant = "error",
  children,
  className,
}: {
  variant?: "error" | "success" | "warning" | "info";
  children: React.ReactNode;
  className?: string;
}) {
  const Icon =
    variant === "error"
      ? AlertCircle
      : variant === "warning"
        ? AlertTriangle
        : variant === "info"
          ? Info
          : CheckCircle2;

  const styles = {
    error: "border-red-200 bg-red-50 text-red-800 [&>svg]:text-red-500",
    success: "border-emerald-200 bg-emerald-50 text-emerald-800 [&>svg]:text-emerald-600",
    warning: "border-amber-200 bg-amber-50 text-amber-900 [&>svg]:text-amber-600",
    info: "border-brand-200 bg-brand-50 text-brand-900 [&>svg]:text-brand-600",
  };

  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-sm font-medium animate-fade-in",
        styles[variant],
        className,
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}
