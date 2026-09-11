import { clearLocalAuthSession, createClient } from "@/lib/supabase/client";

export type BackendHealth = {
  status: "ok";
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;

  constructor(
    message: string,
    status: number,
    code: string | null,
    requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function apiUrl(path: string): string {
  return new URL(path, baseUrl).toString();
}

let redirectingToLogin = false;

async function redirectToLogin() {
  if (redirectingToLogin || typeof window === "undefined") return;
  redirectingToLogin = true;
  try {
    await clearLocalAuthSession();
  } catch {
    // If local/session cleanup fails, still leave the protected route. The
    // backend has already rejected the token, so showing authenticated UI
    // would be misleading.
  }
  window.location.href = "/auth/login";
}

async function getAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

let refreshInFlight: Promise<string | null> | null = null;

/**
 * Shared across concurrent callers: if several requests 401 at the same
 * moment, they all await the same refresh instead of each calling
 * `refreshSession()` independently, which would risk Supabase's
 * refresh-token rotation invalidating a sibling in-flight refresh.
 */
async function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    const supabase = createClient();
    refreshInFlight = supabase.auth
      .refreshSession()
      .then(({ data, error }) => (error ? null : data.session?.access_token ?? null))
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

type ApiRequestInit = Omit<RequestInit, "headers"> & {
  headers?: Record<string, string>;
  idempotencyKey?: string;
};

/**
 * The one authoritative client the whole app uses to call the backend.
 * Attaches the current Supabase access token as a bearer credential (never
 * a client-supplied user id), retries exactly once through a session
 * refresh on 401, and redirects to login if that refresh also fails --
 * centralized here so no page duplicates token/401 handling.
 */
export async function apiRequest<T>(
  path: string,
  init: ApiRequestInit = {},
): Promise<{ data: T; requestId: string | null }> {
  const { idempotencyKey, headers: extraHeaders, ...rest } = init;

  async function doFetch(accessToken: string | null): Promise<Response> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...extraHeaders,
    };
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    return fetch(apiUrl(path), { ...rest, headers });
  }

  const token = await getAccessToken();
  let response = await doFetch(token);

  if (response.status === 401) {
    // A missing token right after sign-in can just mean the session is
    // still settling into storage -- give it one fair retry via refresh
    // before treating this as an unrecoverable auth failure.
    const refreshed = await refreshAccessToken();
    response = refreshed ? await doFetch(refreshed) : response;
  }

  const requestId = response.headers.get("x-request-id");

  if (!response.ok) {
    let message = "Request failed";
    let code: string | null = null;
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code ?? null;
    } catch {
      message = response.statusText || message;
    }
    if (response.status === 401) {
      void redirectToLogin();
    }
    throw new ApiError(message, response.status, code, requestId);
  }

  if (response.status === 204) {
    return { data: undefined as T, requestId };
  }
  return { data: (await response.json()) as T, requestId };
}

export async function getBackendHealth() {
  return apiRequest<BackendHealth>("/api/v1/health");
}
