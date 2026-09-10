"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  HelpCircle,
  LayoutDashboard,
  Loader2,
  LogOut,
  Settings,
  UserCircle,
  Users,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/client";
import { useWorkspace, WorkspaceProvider } from "@/lib/workspace-context";

const navigation = [
  { href: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/app/team", label: "Team", icon: Users },
];

function AppShellInner({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const {
    workspaces,
    activeWorkspaceId,
    activeWorkspace,
    isLoading,
    switchWorkspace,
  } = useWorkspace();
  const [userMenuOpen, setUserMenuOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && workspaces.length === 0) {
      router.replace("/onboarding/workspace");
    }
  }, [isLoading, workspaces.length, router]);

  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50">
        <Loader2
          className="h-6 w-6 animate-spin text-slate-400"
          aria-hidden="true"
        />
      </div>
    );
  }

  if (workspaces.length === 0) {
    // The redirect effect above will navigate away; render nothing meanwhile
    // rather than flashing an empty authenticated shell.
    return null;
  }

  async function handleLogout() {
    const supabase = createClient();
    await supabase.auth.signOut();
    // Clear every cached response so a subsequent sign-in never shows a
    // previous session's (or workspace's) stale data.
    queryClient.clear();
    router.push("/auth/login");
    router.refresh();
  }

  function handleWorkspaceChange(event: React.ChangeEvent<HTMLSelectElement>) {
    switchWorkspace(event.target.value);
    // Never carry a foreign resource id (e.g. a campaign id from the old
    // workspace) across the switch -- always land on a workspace-neutral
    // route.
    router.push("/app/dashboard");
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-slate-200 bg-white lg:block">
        <div className="flex h-16 items-center border-b border-slate-200 px-5">
          <Link href="/app/dashboard" className="text-base font-semibold text-slate-950">
            Outreach
          </Link>
        </div>
        <nav className="space-y-1 px-3 py-4">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium ${
                  active
                    ? "bg-slate-100 text-slate-950"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-950"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-8">
          <div className="min-w-0">
            <label htmlFor="workspace-select" className="sr-only">
              Select workspace
            </label>
            <select
              id="workspace-select"
              value={activeWorkspaceId ?? ""}
              onChange={handleWorkspaceChange}
              className="h-9 max-w-[220px] truncate rounded-md border border-slate-200 bg-white px-2 text-sm font-semibold text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600"
            >
              {workspaces.map((workspace) => (
                <option key={workspace.workspace_id} value={workspace.workspace_id}>
                  {workspace.workspace_name}
                </option>
              ))}
            </select>
            {activeWorkspace ? (
              <p className="mt-0.5 truncate text-xs text-slate-500">
                Role: {activeWorkspace.role_code}
              </p>
            ) : null}
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="Notifications">
              <Bell className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button variant="ghost" size="icon" aria-label="Help">
              <HelpCircle className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button variant="ghost" size="icon" aria-label="Settings">
              <Settings className="h-4 w-4" aria-hidden="true" />
            </Button>
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="User menu"
                aria-expanded={userMenuOpen}
                aria-haspopup="menu"
                onClick={() => setUserMenuOpen((open) => !open)}
              >
                <UserCircle className="h-4 w-4" aria-hidden="true" />
              </Button>
              {userMenuOpen ? (
                <div
                  role="menu"
                  className="absolute right-0 mt-2 w-40 rounded-md border border-slate-200 bg-white p-1 shadow-md"
                >
                  <button
                    role="menuitem"
                    type="button"
                    onClick={handleLogout}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
                  >
                    <LogOut className="h-4 w-4" aria-hidden="true" />
                    Log out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </header>
        <div className="px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <AppShellInner>{children}</AppShellInner>
    </WorkspaceProvider>
  );
}
