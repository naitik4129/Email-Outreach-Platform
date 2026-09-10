"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, Users } from "lucide-react";

import { useWorkspace } from "@/lib/workspace-context";
import { listWorkspaceMembers } from "@/lib/workspaces-api";

export default function TeamPage() {
  const { activeWorkspaceId } = useWorkspace();

  const { data: members, isLoading, isError } = useQuery({
    queryKey: ["workspace", activeWorkspaceId, "members"],
    queryFn: () => listWorkspaceMembers(activeWorkspaceId!),
    enabled: Boolean(activeWorkspaceId),
  });

  return (
    <main className="space-y-6">
      <div>
        <p className="text-sm font-medium text-teal-700">Workspace</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-normal text-slate-950">
          Team
        </h1>
      </div>

      <section className="rounded-md border border-slate-200 bg-white shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 p-8 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading team members&hellip;
          </div>
        ) : isError ? (
          <p className="p-8 text-center text-sm text-red-600">
            We couldn&apos;t load the team directory. Please try again.
          </p>
        ) : !members || members.length === 0 ? (
          <div className="flex flex-col items-center gap-2 p-8 text-center text-sm text-slate-500">
            <Users className="h-6 w-6 text-slate-300" aria-hidden="true" />
            No team members yet.
          </div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-xs font-medium uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-3">Name</th>
                <th className="px-5 py-3">Role</th>
                <th className="px-5 py-3">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((member) => (
                <tr key={member.membership_id}>
                  <td className="px-5 py-3 font-medium text-slate-950">
                    {member.display_name ?? "Unnamed member"}
                  </td>
                  <td className="px-5 py-3 text-slate-600">{member.role_code}</td>
                  <td className="px-5 py-3 text-slate-600">{member.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
