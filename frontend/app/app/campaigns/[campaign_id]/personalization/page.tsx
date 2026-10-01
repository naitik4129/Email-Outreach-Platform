import { redirect } from "next/navigation";

// The Personalization tab was merged into the Sequence tab (ADR-0016). Old
// bookmarks and links land there instead of on a dead page.
export default async function PersonalizationRedirect({
  params,
}: {
  params: Promise<{ campaign_id: string }>;
}) {
  const { campaign_id: campaignId } = await params;
  redirect(`/app/campaigns/${encodeURIComponent(campaignId)}/sequence`);
}
