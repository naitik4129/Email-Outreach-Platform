export type BackendHealth = {
  status: "ok";
};

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function apiUrl(path: string): string {
  return new URL(path, baseUrl).toString();
}

export async function apiRequest<T>(path: string, init: RequestInit = {}) {
  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  const requestId = response.headers.get("x-request-id");
  if (!response.ok) {
    let message = "Request failed";
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
    } catch {
      message = response.statusText || message;
    }
    throw new ApiError(message, response.status, requestId);
  }
  return { data: (await response.json()) as T, requestId };
}

export async function getBackendHealth() {
  return apiRequest<BackendHealth>("/api/v1/health");
}

