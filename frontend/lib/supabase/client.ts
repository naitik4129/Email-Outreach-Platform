import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | null = null;

export function createClient() {
  browserClient ??= createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  ) as SupabaseClient;
  return browserClient;
}

export async function clearLocalAuthSession() {
  await createClient().auth.signOut({ scope: "local" });
}

export async function signOutCurrentBrowser() {
  const supabase = createClient();
  const { error } = await supabase.auth.signOut();
  if (error) {
    await supabase.auth.signOut({ scope: "local" });
  }
}
