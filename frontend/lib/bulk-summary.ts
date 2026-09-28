import type { BulkActionResult } from "@/types/domain";

// One sentence for a toast. A partial result names the first reason so the user
// knows why some rows were left alone instead of seeing a bare count.
export function summarizeBulk(result: BulkActionResult, verb: string): string {
  const { succeeded, failed, results } = result;
  if (failed === 0) return `${verb} ${succeeded} item${succeeded === 1 ? "" : "s"}.`;
  const firstReason = results.find((r) => !r.ok)?.message;
  const head =
    succeeded === 0
      ? `Nothing was ${verb.toLowerCase()}.`
      : `${verb} ${succeeded}, ${failed} could not be changed.`;
  return firstReason ? `${head} ${firstReason}` : head;
}
