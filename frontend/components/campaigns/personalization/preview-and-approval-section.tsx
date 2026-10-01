"use client";

import { ApprovalBar } from "@/components/campaigns/personalization/approval-bar";
import { SamplePreviewPanel } from "@/components/campaigns/personalization/sample-preview-panel";
import type { usePersonalizationState } from "@/components/campaigns/personalization/use-personalization-state";
import { Alert } from "@/components/ui/alert";
import { canApprovePersonalization } from "@/lib/permissions";
import type { PersonalizationState, RoleCode } from "@/types/domain";

type Hook = ReturnType<typeof usePersonalizationState>;

type Props = {
  workspaceId: string | null | undefined;
  campaignId: string;
  state: PersonalizationState;
  personalization: Pick<Hook, "preview" | "approveMutation" | "approveError">;
  role: RoleCode | undefined;
  mayDraft: boolean;
  readOnly: boolean;
  // True when the emails or the setup changed since the samples were approved.
  changedSinceApproval: boolean;
};

/** Sample previews and the manager approval, shown inline under the emails. */
export function PreviewAndApprovalSection({
  workspaceId,
  campaignId,
  state,
  personalization,
  role,
  mayDraft,
  readOnly,
  changedSinceApproval,
}: Props) {
  const { preview, approveMutation, approveError } = personalization;
  const blockedReason = !state.config
    ? "Save the campaign objective first, then generate samples."
    : null;
  return (
    <section id="preview" aria-label="Preview and approve" className="space-y-4">
      {changedSinceApproval ? (
        <Alert variant="warning">
          The setup or the emails changed. Generate new samples and approve them again before you
          launch.
        </Alert>
      ) : null}
      <SamplePreviewPanel
        workspaceId={workspaceId}
        campaignId={campaignId}
        batch={preview.batch}
        loading={preview.query.isLoading}
        loadError={preview.query.error}
        canGenerate={mayDraft && !readOnly}
        blockedReason={blockedReason}
        generating={preview.generate.isPending}
        generateError={preview.generate.error}
        onGenerate={(audienceMemberId) => preview.generate.mutateAsync(audienceMemberId)}
      />
      <ApprovalBar
        approval={state.approval}
        batch={preview.batch}
        canApprove={canApprovePersonalization(role)}
        readOnly={readOnly}
        approving={approveMutation.isPending}
        error={approveError}
        onApprove={() => approveMutation.mutate()}
      />
    </section>
  );
}
