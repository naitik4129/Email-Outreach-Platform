import { addLeadListMember } from "@/lib/leads-api";

export type BulkAddProgress = { processed: number; total: number; failed: number };

// No bulk membership endpoint exists yet (POST /lead-lists/{id}/members takes
// one lead_id at a time), so this runs a small pool of concurrent single-add
// calls with a progress callback. Both add-list-members-dialog.tsx and
// create-list-from-filter-dialog.tsx share this so a future bulk endpoint
// only needs to change in one place.
export async function addLeadsToListWithProgress(
  workspaceId: string,
  listId: string,
  leadIds: string[],
  options: { concurrency?: number; onProgress?: (progress: BulkAddProgress) => void } = {},
): Promise<{ succeeded: number; failed: number }> {
  const concurrency = Math.max(1, Math.min(options.concurrency ?? 5, leadIds.length || 1));
  const queue = [...leadIds];
  let processed = 0;
  let succeeded = 0;
  let failed = 0;

  async function worker() {
    let leadId: string | undefined;
    while ((leadId = queue.shift())) {
      try {
        await addLeadListMember(workspaceId, listId, leadId);
        succeeded += 1;
      } catch {
        failed += 1;
      }
      processed += 1;
      options.onProgress?.({ processed, total: leadIds.length, failed });
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  return { succeeded, failed };
}
