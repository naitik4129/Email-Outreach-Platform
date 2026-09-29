"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BarChart3,
  Bell,
  Ban,
  Check,
  CheckCheck,
  ChevronDown,
  Contact,
  CreditCard,
  FileText,
  HelpCircle,
  Inbox,
  LayoutDashboard,
  Loader2,
  LogOut,
  Mail,
  Menu,
  Plus,
  Send,
  Settings,
  ShieldCheck,
  Sliders,
  UploadCloud,
  UserCircle,
  Users,
  X,
} from "lucide-react";

import { LogoMark, Logo } from "@/components/brand/logo";
import { Button } from "@/components/ui/button";
import { ToastProvider } from "@/components/ui/toast";
import { NewWorkspaceDialog } from "@/components/workspace/new-workspace-dialog";
import { ApiError } from "@/lib/api-client";
import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "@/lib/notifications-api";
import { canDeleteWorkspace } from "@/lib/permissions";
import { signOutCurrentBrowser } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { useWorkspace, WorkspaceProvider } from "@/lib/workspace-context";

type NavItem = { href: string; label: string; icon: typeof Send };
type NavGroup = { label: string | null; items: NavItem[] };

const navigationGroups: NavGroup[] = [
  {
    label: null,
    items: [{ href: "/app/dashboard", label: "Dashboard", icon: LayoutDashboard }],
  },
  {
    label: "Outreach",
    items: [
      { href: "/app/campaigns", label: "Campaigns", icon: Send },
      { href: "/app/inbox", label: "Inbox", icon: Inbox },
      { href: "/app/templates", label: "Templates", icon: FileText },
    ],
  },
  {
    label: "Audience",
    items: [
      { href: "/app/leads", label: "Leads", icon: Contact },
      { href: "/app/leads/imports", label: "Imports", icon: UploadCloud },
      { href: "/app/leads/suppression", label: "Suppression", icon: Ban },
    ],
  },
  {
    label: "Insights",
    items: [
      { href: "/app/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/app/deliverability", label: "Deliverability", icon: ShieldCheck },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/app/mailboxes", label: "Mailboxes", icon: Mail },
      { href: "/app/team", label: "Team", icon: Users },
    ],
  },
];

const allNavItems = navigationGroups.flatMap((group) => group.items);

// Most specific match wins, so /app/leads/imports highlights Imports only and
// not Leads as well.
function activeNavHref(pathname: string | null) {
  if (!pathname) return null;
  const matches = allNavItems.filter(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  matches.sort((a, b) => b.href.length - a.href.length);
  return matches[0]?.href ?? null;
}

function SidebarNav({
  pathname,
  onNavigate,
}: {
  pathname: string | null;
  onNavigate?: () => void;
}) {
  const activeHref = activeNavHref(pathname);
  return (
    <nav aria-label="Primary" className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
      {navigationGroups.map((group, index) => (
        <div key={group.label ?? index}>
          {group.label ? (
            <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
              {group.label}
            </p>
          ) : null}
          <div className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon;
              const active = item.href === activeHref;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative flex h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500",
                    active
                      ? "bg-brand-50 text-brand-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                  )}
                >
                  {active ? (
                    <span
                      aria-hidden="true"
                      className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-brand-600"
                    />
                  ) : null}
                  <Icon
                    className={cn(
                      "h-[18px] w-[18px] shrink-0 transition-colors",
                      active ? "text-brand-600" : "text-slate-400 group-hover:text-slate-600",
                    )}
                    aria-hidden="true"
                  />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

type OpenMenu = "notifications" | "settings" | "user" | null;

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
  const [openMenu, setOpenMenu] = useState<OpenMenu>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [newWorkspaceDialogOpen, setNewWorkspaceDialogOpen] = useState(false);
  const headerActionsRef = useRef<HTMLDivElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const notificationsOpen = openMenu === "notifications";
  const settingsMenuOpen = openMenu === "settings";
  const userMenuOpen = openMenu === "user";

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

  // Close any open header menu / the mobile drawer whenever the route changes.
  useEffect(() => {
    setOpenMenu(null);
    setDrawerOpen(false);
  }, [pathname]);

  // Header menus close on outside click and Escape.
  useEffect(() => {
    if (!openMenu) return;
    function onPointerDown(event: MouseEvent) {
      if (
        headerActionsRef.current &&
        !headerActionsRef.current.contains(event.target as Node)
      ) {
        setOpenMenu(null);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpenMenu(null);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openMenu]);

  // Mobile drawer: lock page scroll, close on Escape, move focus into it.
  useEffect(() => {
    if (!drawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    drawerRef.current?.querySelector<HTMLElement>("a,button")?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setDrawerOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [drawerOpen]);

  // No separate 401-recovery effect here: apiRequest (lib/api-client.ts)
  // already clears the session and hard-redirects to /auth/login the
  // moment the underlying request 401s. A second, independent recovery
  // flow here would race that one. The card below is just the interim UI
  // while that redirect is in flight.

  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50">
        <div className="flex flex-col items-center gap-3">
          <LogoMark className="h-10 w-10 animate-pulse" />
          <Loader2
            className="h-4 w-4 animate-spin text-slate-400"
            aria-hidden="true"
          />
        </div>
      </div>
    );
  }

  if (workspaceError) {
    const sessionExpired =
      workspaceError instanceof ApiError && workspaceError.status === 401;
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 px-4">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <Logo size="sm" className="mb-4" />
          <h1 className="text-base font-semibold text-slate-900">
            {sessionExpired ? "Session expired" : "Unable to load workspace"}
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            {sessionExpired
              ? "Sign in again to continue."
              : "Refresh the page or try again in a moment."}
          </p>
          <Button
            type="button"
            className="mt-5 w-full"
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

  function toggleMenu(menu: Exclude<OpenMenu, null>) {
    setOpenMenu((current) => (current === menu ? null : menu));
  }

  const menuItemClasses =
    "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100 focus-visible:bg-slate-100 focus-visible:outline-none";

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[70] focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-brand-700 focus:shadow-overlay"
      >
        Skip to content
      </a>

      <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="flex h-16 shrink-0 items-center border-b border-slate-100 px-5">
          <Link
            href="/app/dashboard"
            aria-label="Outly dashboard"
            className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <Logo />
          </Link>
        </div>
        <SidebarNav pathname={pathname} />
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-slate-900/50 animate-fade-in"
            onMouseDown={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <div
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="relative flex h-full w-72 max-w-[85vw] flex-col bg-white shadow-overlay animate-slide-in-left"
          >
            <div className="flex h-16 shrink-0 items-center justify-between border-b border-slate-100 pl-5 pr-3">
              <Logo />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Close navigation"
                onClick={() => setDrawerOpen(false)}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
            <SidebarNav pathname={pathname} onNavigate={() => setDrawerOpen(false)} />
          </div>
        </div>
      ) : null}

      <div className="lg:pl-64">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Open navigation"
              aria-expanded={drawerOpen}
              className="lg:hidden"
              onClick={() => setDrawerOpen(true)}
            >
              <Menu className="h-5 w-5" aria-hidden="true" />
            </Button>
            <Link
              href="/app/dashboard"
              aria-label="Outly dashboard"
              className="rounded-md lg:hidden"
            >
              <LogoMark />
            </Link>
            <div className="min-w-0">
              <label htmlFor="workspace-select" className="sr-only">
                Select workspace
              </label>
              <div className="flex items-center gap-1.5">
                <div className="relative min-w-0">
                  <select
                    id="workspace-select"
                    value={activeWorkspaceId ?? ""}
                    onChange={handleWorkspaceChange}
                    className="h-9 w-full max-w-[220px] cursor-pointer appearance-none truncate rounded-lg border border-slate-200 bg-white py-0 pl-3 pr-8 text-sm font-semibold text-slate-900 shadow-sm transition-colors hover:border-slate-300 focus-visible:border-brand-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
                  >
                    {workspaces.map((workspace) => (
                      <option key={workspace.workspace_id} value={workspace.workspace_id}>
                        {workspace.workspace_name}
                      </option>
                    ))}
                  </select>
                  <ChevronDown
                    className="pointer-events-none absolute right-2.5 top-2.5 h-4 w-4 text-slate-400"
                    aria-hidden="true"
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  aria-label="New workspace"
                  title="New workspace"
                  className="h-9 w-9 shrink-0"
                  onClick={() => setNewWorkspaceDialogOpen(true)}
                >
                  <Plus className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
              {activeWorkspace ? (
                <p className="mt-0.5 hidden truncate text-xs text-slate-500 sm:block">
                  Role: {activeWorkspace.role_code}
                </p>
              ) : null}
            </div>
          </div>
          <div ref={headerActionsRef} className="flex items-center gap-1">
            {/* Notifications Popover */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Notifications"
                aria-expanded={notificationsOpen}
                aria-haspopup="dialog"
                className="relative"
                onClick={() => toggleMenu("notifications")}
              >
                <Bell className="h-[18px] w-[18px]" aria-hidden="true" />
                {unreadCount > 0 && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-brand-600 px-1 text-[10px] font-bold text-white ring-2 ring-white">
                    {unreadCount > 99 ? "99+" : unreadCount}
                  </span>
                )}
              </Button>

              {notificationsOpen && (
                <div
                  role="dialog"
                  aria-label="Notifications"
                  className="fixed inset-x-3 top-[4.25rem] z-50 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-overlay animate-pop-in sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96"
                >
                  <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/80 px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-slate-900">
                        Notifications
                      </span>
                      {unreadCount > 0 && (
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs font-semibold text-brand-800">
                          {unreadCount} new
                        </span>
                      )}
                    </div>
                    {unreadCount > 0 && (
                      <button
                        type="button"
                        disabled={markAllReadMutation.isPending}
                        onClick={() => markAllReadMutation.mutate()}
                        className="flex items-center gap-1 rounded text-xs font-medium text-brand-700 hover:text-brand-900 disabled:opacity-50"
                      >
                        <CheckCheck className="h-3.5 w-3.5" aria-hidden="true" />
                        Mark all read
                      </button>
                    )}
                  </div>

                  <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto">
                    {loadingNotifications ? (
                      <div className="flex items-center justify-center gap-2 p-8 text-xs text-slate-500">
                        <Loader2 className="h-4 w-4 animate-spin text-brand-600" aria-hidden="true" />
                        Loading updates&hellip;
                      </div>
                    ) : !recentNotifications || recentNotifications.length === 0 ? (
                      <div className="p-8 text-center">
                        <Bell className="mx-auto h-6 w-6 text-slate-300" aria-hidden="true" />
                        <p className="mt-2 text-sm font-medium text-slate-700">
                          You&apos;re all caught up
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          No notifications yet.
                        </p>
                      </div>
                    ) : (
                      recentNotifications.map((n) => (
                        <div
                          key={n.id}
                          className={cn(
                            "p-3.5 transition-colors hover:bg-slate-50",
                            n.is_read ? "bg-white" : "bg-brand-50/40",
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <h4 className="truncate text-[13px] font-semibold text-slate-900">
                              {n.title}
                            </h4>
                            {!n.is_read && (
                              <button
                                type="button"
                                onClick={() =>
                                  markNotificationReadMutation.mutate(n.id)
                                }
                                className="rounded p-0.5 text-slate-400 hover:text-brand-700"
                                title="Mark read"
                                aria-label="Mark read"
                              >
                                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                              </button>
                            )}
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs text-slate-600">
                            {n.body}
                          </p>
                          <p className="mt-1.5 text-[11px] text-slate-400">
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
                      onClick={() => setOpenMenu(null)}
                      className="text-xs font-medium text-brand-700 hover:text-brand-900"
                    >
                      View all notifications &rarr;
                    </Link>
                  </div>
                </div>
              )}
            </div>

            <Button
              variant="ghost"
              size="icon"
              aria-label="Help"
              className="hidden sm:inline-flex"
            >
              <HelpCircle className="h-[18px] w-[18px]" aria-hidden="true" />
            </Button>

            {/* Settings Dropdown */}
            <div className="relative">
              <Button
                variant="ghost"
                size="icon"
                aria-label="Settings"
                aria-expanded={settingsMenuOpen}
                aria-haspopup="menu"
                onClick={() => toggleMenu("settings")}
              >
                <Settings className="h-[18px] w-[18px]" aria-hidden="true" />
              </Button>

              {settingsMenuOpen && (
                <div
                  role="menu"
                  className="absolute right-0 z-50 mt-2 w-56 rounded-xl border border-slate-200 bg-white p-1 shadow-overlay animate-pop-in"
                >
                  <Link
                    href="/app/settings/usage"
                    role="menuitem"
                    onClick={() => setOpenMenu(null)}
                    className={menuItemClasses}
                  >
                    <CreditCard className="h-4 w-4 text-slate-500" aria-hidden="true" />
                    Usage &amp; Quotas
                  </Link>
                  <Link
                    href="/app/settings/notifications"
                    role="menuitem"
                    onClick={() => setOpenMenu(null)}
                    className={menuItemClasses}
                  >
                    <Sliders className="h-4 w-4 text-slate-500" aria-hidden="true" />
                    Notification Settings
                  </Link>
                  <Link
                    href="/app/team"
                    role="menuitem"
                    onClick={() => setOpenMenu(null)}
                    className={menuItemClasses}
                  >
                    <Users className="h-4 w-4 text-slate-500" aria-hidden="true" />
                    Team &amp; Access
                  </Link>
                  {canDeleteWorkspace(activeWorkspace?.role_code) ? (
                    <Link
                      href="/app/settings/danger"
                      role="menuitem"
                      onClick={() => setOpenMenu(null)}
                      className={menuItemClasses}
                    >
                      <AlertTriangle className="h-4 w-4 text-red-500" aria-hidden="true" />
                      Danger Zone
                    </Link>
                  ) : null}
                </div>
              )}
            </div>

            {/* User Profile Menu */}
            <div className="relative ml-1">
              <button
                type="button"
                aria-label="User menu"
                aria-expanded={userMenuOpen}
                aria-haspopup="menu"
                onClick={() => toggleMenu("user")}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm transition-colors hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2"
              >
                <UserCircle className="h-5 w-5" aria-hidden="true" />
              </button>
              {userMenuOpen ? (
                <div
                  role="menu"
                  className="absolute right-0 z-50 mt-2 w-44 rounded-xl border border-slate-200 bg-white p-1 shadow-overlay animate-pop-in"
                >
                  <button
                    role="menuitem"
                    type="button"
                    onClick={handleLogout}
                    className={menuItemClasses}
                  >
                    <LogOut className="h-4 w-4 text-slate-500" aria-hidden="true" />
                    Log out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </header>
        <div
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1440px] px-4 py-6 outline-none sm:px-6 lg:px-8"
        >
          {children}
        </div>
      </div>
      <NewWorkspaceDialog
        open={newWorkspaceDialogOpen}
        onRequestClose={() => setNewWorkspaceDialogOpen(false)}
      />
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <ToastProvider>
        <AppShellInner>{children}</AppShellInner>
      </ToastProvider>
    </WorkspaceProvider>
  );
}
