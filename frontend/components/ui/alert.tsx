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
    error: "border-red-200 bg-red-50 text-red-700",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
    info: "border-blue-200 bg-blue-50 text-blue-700",
  };

  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2 rounded-md border p-3 text-sm font-medium",
        styles[variant],
        className,
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}
