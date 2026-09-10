import type { LucideIcon } from "lucide-react";

export function AuthCard({
  icon: Icon,
  title,
  subtitle,
  children,
  width = "sm",
}: {
  icon: LucideIcon;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  width?: "sm" | "md";
}) {
  return (
    <main className="grid min-h-screen place-items-center bg-slate-50 px-4">
      <section
        className={`w-full ${width === "sm" ? "max-w-sm" : "max-w-md"} rounded-md border border-slate-200 bg-white p-6 shadow-sm`}
      >
        <div className="grid h-10 w-10 place-items-center rounded-md bg-teal-50 text-teal-700">
          <Icon className="h-5 w-5" aria-hidden="true" />
        </div>
        <h1 className="mt-5 text-2xl font-semibold tracking-normal text-slate-950">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
        ) : null}
        <div className="mt-6">{children}</div>
      </section>
    </main>
  );
}
