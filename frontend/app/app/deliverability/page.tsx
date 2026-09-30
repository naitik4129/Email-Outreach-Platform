import { redirect } from "next/navigation";

// Merged into the dashboard; kept so existing bookmarks still resolve.
export default function Page() {
  redirect("/app/dashboard");
}
