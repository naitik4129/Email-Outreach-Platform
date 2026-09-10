"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listWorkspaces } from "@/lib/workspaces-api";
import type { WorkspaceListItem } from "@/types/domain";

const STORAGE_KEY = "active-workspace-id";

type WorkspaceContextValue = {
  workspaces: WorkspaceListItem[];
  activeWorkspaceId: string | null;
  activeWorkspace: WorkspaceListItem | null;
  isLoading: boolean;
  switchWorkspace: (workspaceId: string) => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function readStoredWorkspaceId(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredWorkspaceId(workspaceId: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, workspaceId);
  } catch {
    // Private browsing / storage disabled: active workspace just won't
    // persist across reloads, which is a preference, not a security issue.
  }
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [activeWorkspaceId, setActiveWorkspaceIdState] = useState<string | null>(
    null,
  );

  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
  });

  useEffect(() => {
    if (isLoading) return;
    const stored = readStoredWorkspaceId();
    // The stored id is only ever a preference: re-validate it against the
    // authoritative membership list on every load and drop it if stale.
    const validStored =
      stored && workspaces.some((w) => w.workspace_id === stored) ? stored : null;
    setActiveWorkspaceIdState(validStored ?? workspaces[0]?.workspace_id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, workspaces.map((w) => w.workspace_id).join(",")]);

  function switchWorkspace(workspaceId: string) {
    setActiveWorkspaceIdState(workspaceId);
    writeStoredWorkspaceId(workspaceId);
    // Never let a previous workspace's cached data leak into the new one,
    // and never carry a foreign resource id across the switch either --
    // callers navigate to a fresh route (e.g. /app/dashboard) on switch.
    queryClient.removeQueries({
      predicate: (query) =>
        Array.isArray(query.queryKey) && query.queryKey[0] === "workspace",
    });
  }

  const activeWorkspace = useMemo(
    () => workspaces.find((w) => w.workspace_id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  );

  return (
    <WorkspaceContext.Provider
      value={{
        workspaces,
        activeWorkspaceId,
        activeWorkspace,
        isLoading,
        switchWorkspace,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return ctx;
}
