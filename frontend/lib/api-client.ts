import { createClient } from "@/lib/supabase/client";

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

function redirectToLogin() {
  if (redirectingToLogin || typeof window === "undefined") return;
  redirectingToLogin = true;
  window.location.href = "/auth/login";
}

async function getAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

async function refreshAccessToken(): Promise<string | null> {
  const supabase = createClient();
  const { data, error } = await supabase.auth.refreshSession();
  if (error) return null;
  return data.session?.access_token ?? null;
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

  if (response.status === 401 && token) {
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
      redirectToLogin();
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
