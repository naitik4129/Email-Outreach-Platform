"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createCampaign } from "@/lib/campaigns-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";

export default function NewCampaignPage() {
  const router = useRouter();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);

  const createMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId) throw new Error("No active workspace");
      return createCampaign(activeWorkspaceId, {
        name,
        description: description.trim() ? description : null,
      });
    },
    onSuccess: (campaign) => {
      // Only Basics are collected here -- the rest of configuration happens
      // across the campaign's own tabs, saved incrementally, never in one
      // giant up-front transaction.
      router.push(`/app/campaigns/${campaign.id}/audience`);
    },
    onError: (err) => {
      setFormError(
        err instanceof ApiError ? err.message : "Failed to create campaign.",
      );
    },
  });

  if (!mayDraft) {
    return (
      <div className="mx-auto max-w-xl py-12 text-center">
        <p className="text-sm text-slate-600">
          You do not have permission to create campaigns in this workspace.
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href="/app/campaigns">Back to Campaigns</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <div>
        <Link
          href="/app/campaigns"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Campaigns
        </Link>
        <h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-900">
          Create Campaign
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Start with a name. You&apos;ll configure the audience, sequence, senders,
          and schedule next.
        </p>
      </div>

      {formError && <Alert variant="error">{formError}</Alert>}

      <form
        className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) {
            setFormError("Campaign name is required.");
            return;
          }
          setFormError(null);
          createMutation.mutate();
        }}
      >
        <Field label="Campaign name" required>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Q1 Outbound Sequence"
            maxLength={200}
            autoFocus
          />
        </Field>
        <Field label="Description">
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional context for your team"
            maxLength={2000}
          />
        </Field>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" asChild>
            <Link href="/app/campaigns">Cancel</Link>
          </Button>
          <Button type="submit" disabled={createMutation.isPending}>
            {createMutation.isPending && (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            )}
            Create Campaign
          </Button>
        </div>
      </form>
    </div>
  );
}
