"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Check,
  CheckCheck,
  Contact,
  CreditCard,
  HelpCircle,
  LayoutDashboard,
  Loader2,
  LogOut,
  Send,
  Settings,
  Sliders,
  UserCircle,
  Users,
  UploadCloud,
  Ban,
  FileText,
  Mail,
  Inbox,
  BarChart3,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/notifications-api";
import { signOutCurrentBrowser } from "@/lib/supabase/client";
import { useWorkspace, WorkspaceProvider } from "@/lib/workspace-context";

const navigation = [
  { href: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/app/campaigns", label: "Campaigns", icon: Send },
  { href: "/app/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/app/deliverability", label: "Deliverability", icon: ShieldCheck },
  { href: "/app/mailboxes", label: "Mailboxes", icon: Mail },
  { href: "/app/leads", label: "Leads", icon: Contact },
  { href: "/app/inbox", label: "Inbox", icon: Inbox },
  { href: "/app/templates", label: "Templates", icon: FileText },
  { href: "/app/leads/imports", label: "Imports", icon: UploadCloud },
  { href: "/app/leads/suppression", label: "Suppression", icon: Ban },
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
    error: workspaceError,
    switchWorkspace,
  } = useWorkspace();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [settingsMenuOpen, setSettingsMenuOpen] = useState(false);

  const { data: unreadData } = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "notifications", "unread-count"],
    queryFn: () => getUnreadCount(activeWorkspaceId!),
    enabled: Boolean(activeWorkspaceId),
    refetchInterval: 30000,
  });

  const { data: recentNotifications, isLoading: loadingNotifications } = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "notifications", "recent"],
    queryFn: () => listNotifications(activeWorkspaceId!, { limit: 5 }),
    enabled: Boolean(activeWorkspaceId && notificationsOpen),
  });

  const markNotificationReadMutation = useMutation({
    mutationFn: (id: string) => markNotificationRead(activeWorkspaceId!, id),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "notifications"],
      });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: () => markAllNotificationsRead(activeWorkspaceId!),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace", activeWorkspaceId, "notifications"],
      });
    },
  });

  const unreadCount = unreadData?.unread_count ?? 0;

  useEffect(() => {
    if (!isLoading && !workspaceError && workspaces.length === 0) {
      router.replace("/onboarding/workspace");
    }
  }, [isLoading, workspaceError, workspaces.length, router]);

  // No separate 401-recovery effect here: apiRequest (lib/api-client.ts)
  // already clears the session and hard-redirects to /auth/login the
  // moment the underlying request 401s. A second, independent recovery
  // flow here would race that one. The card below is just the interim UI
  // while that redirect is in flight.

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

  if (workspaceError) {
    const sessionExpired =
      workspaceError instanceof ApiError && workspaceError.status === 401;
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 px-4">
        <div className="w-full max-w-sm rounded-md border border-slate-200 bg-white p-5 shadow-sm">
          <h1 className="text-base font-semibold text-slate-950">
            {sessionExpired ? "Session expired" : "Unable to load workspace"}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {sessionExpired
              ? "Sign in again to continue."
              : "Refresh the page or try again in a moment."}
          </p>
          <Button
            type="button"
            className="mt-4 w-full"
            onClick={() => {
              router.push(sessionExpired ? "/auth/login" : "/app");
              router.refresh();
            }}
          >
            {sessionExpired ? "Sign in" : "Retry"}
          </Button>
        </div>
      </div>
    );
  }

  if (workspaces.length === 0) {
    // The redirect effect above will navigate away; render nothing meanwhile
    // rather than flashing an empty authenticated shell.
    return null;
  }

  async function handleLogout() {
    await signOutCurrentBrowser();
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
            {/* Notifications Popover */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Notifications"
                aria-expanded={notificationsOpen}
                aria-haspopup="dialog"
                className="relative"
                onClick={() => {
                  setNotificationsOpen((open) => !open);
                  setSettingsMenuOpen(false);
                  setUserMenuOpen(false);
                }}
              >
                <Bell className="h-4 w-4" aria-hidden="true" />
                {unreadCount > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-teal-600 px-1 text-[10px] font-bold text-white ring-2 ring-white">
                    {unreadCount > 99 ? "99+" : unreadCount}
                  </span>
                )}
              </Button>

              {notificationsOpen && (
                <div
                  role="dialog"
                  aria-label="Notifications"
                  className="absolute right-0 mt-2 w-80 sm:w-96 rounded-lg border border-slate-200 bg-white shadow-xl z-50 overflow-hidden"
                >
                  <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/80 px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-slate-900">
                        Notifications
                      </span>
                      {unreadCount > 0 && (
                        <span className="rounded-full bg-teal-100 px-2 py-0.5 text-xs font-semibold text-teal-800">
                          {unreadCount} new
                        </span>
                      )}
                    </div>
                    {unreadCount > 0 && (
                      <button
                        type="button"
                        disabled={markAllReadMutation.isPending}
                        onClick={() => markAllReadMutation.mutate()}
                        className="text-xs font-medium text-teal-700 hover:text-teal-900 flex items-center gap-1"
                      >
                        <CheckCheck className="h-3.5 w-3.5" />
                        Mark all read
                      </button>
                    )}
                  </div>

                  <div className="max-h-80 overflow-y-auto divide-y divide-slate-100">
                    {loadingNotifications ? (
                      <div className="flex items-center justify-center gap-2 p-8 text-xs text-slate-500">
                        <Loader2 className="h-4 w-4 animate-spin text-teal-600" />
                        Loading updates&hellip;
                      </div>
                    ) : !recentNotifications || recentNotifications.length === 0 ? (
                      <div className="p-8 text-center text-xs text-slate-500">
                        No notifications yet.
                      </div>
                    ) : (
                      recentNotifications.map((n) => (
                        <div
                          key={n.id}
                          className={`p-3.5 transition-colors ${
                            n.is_read ? "bg-white" : "bg-teal-50/40"
                          } hover:bg-slate-50`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <h4 className="text-xs font-semibold text-slate-900 truncate">
                              {n.title}
                            </h4>
                            {!n.is_read && (
                              <button
                                type="button"
                                onClick={() =>
                                  markNotificationReadMutation.mutate(n.id)
                                }
                                className="text-slate-400 hover:text-teal-700"
                                title="Mark read"
                              >
                                <Check className="h-3 w-3" />
                              </button>
                            )}
                          </div>
                          <p className="mt-1 text-xs text-slate-600 line-clamp-2">
                            {n.body}
                          </p>
                          <p className="mt-1.5 text-[10px] text-slate-400">
                            {new Date(n.created_at).toLocaleDateString(
                              undefined,
                              {
                                month: "short",
                                day: "numeric",
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}
                          </p>
                        </div>
                      ))
                    )}
                  </div>

                  <div className="border-t border-slate-100 bg-slate-50 px-4 py-2.5 text-center">
                    <Link
                      href="/app/notifications"
                      onClick={() => setNotificationsOpen(false)}
                      className="text-xs font-medium text-teal-700 hover:text-teal-900"
                    >
                      View all notifications &rarr;
                    </Link>
                  </div>
                </div>
              )}
            </div>

            <Button variant="ghost" size="icon" aria-label="Help">
              <HelpCircle className="h-4 w-4" aria-hidden="true" />
            </Button>

            {/* Settings Dropdown */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Settings"
                aria-expanded={settingsMenuOpen}
                aria-haspopup="menu"
                onClick={() => {
                  setSettingsMenuOpen((open) => !open);
                  setNotificationsOpen(false);
                  setUserMenuOpen(false);
                }}
              >
                <Settings className="h-4 w-4" aria-hidden="true" />
              </Button>

              {settingsMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 mt-2 w-52 rounded-md border border-slate-200 bg-white p-1 shadow-md z-50"
                >
                  <Link
                    href="/app/settings/usage"
                    role="menuitem"
                    onClick={() => setSettingsMenuOpen(false)}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
                  >
                    <CreditCard className="h-4 w-4 text-slate-500" />
                    Usage &amp; Quotas
                  </Link>
                  <Link
                    href="/app/settings/notifications"
                    role="menuitem"
                    onClick={() => setSettingsMenuOpen(false)}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
                  >
                    <Sliders className="h-4 w-4 text-slate-500" />
                    Notification Settings
                  </Link>
                  <Link
                    href="/app/team"
                    role="menuitem"
                    onClick={() => setSettingsMenuOpen(false)}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-100"
                  >
                    <Users className="h-4 w-4 text-slate-500" />
                    Team &amp; Access
                  </Link>
                </div>
              )}
            </div>

            {/* User Profile Menu */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="User menu"
                aria-expanded={userMenuOpen}
                aria-haspopup="menu"
                onClick={() => {
                  setUserMenuOpen((open) => !open);
                  setNotificationsOpen(false);
                  setSettingsMenuOpen(false);
                }}
              >
                <UserCircle className="h-4 w-4" aria-hidden="true" />
              </Button>
              {userMenuOpen ? (
                <div
                  role="menu"
                  className="absolute right-0 mt-2 w-40 rounded-md border border-slate-200 bg-white p-1 shadow-md z-50"
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
