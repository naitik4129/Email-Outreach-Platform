import { afterEach, describe, expect, it, vi } from "vitest";

const getSession = vi.fn();
const refreshSession = vi.fn();

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({
    auth: { getSession, refreshSession },
  }),
}));

import { apiRequest, ApiError } from "@/lib/api-client";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("apiRequest", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("attaches the current access token as a bearer credential", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "token-123" } } });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/me");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer token-123");
  });

  it("sends no Authorization header when there is no session", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/health");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("retries once through a session refresh on 401, then succeeds", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "expired" } } });
    refreshSession.mockResolvedValue({
      data: { session: { access_token: "fresh" } },
      error: null,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "unauthenticated", message: "expired" } }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest("/api/v1/me");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer fresh");
    expect(result.data).toEqual({ ok: true });
  });

  it("throws ApiError with the server message when refresh also fails", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "expired" } } });
    refreshSession.mockResolvedValue({ data: { session: null }, error: new Error("no") });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(401, { error: { code: "unauthenticated", message: "Invalid or expired session" } }),
      );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", { location: { href: "" } } as unknown as Window & typeof globalThis);

    await expect(apiRequest("/api/v1/me")).rejects.toMatchObject({
      status: 401,
      message: "Invalid or expired session",
    });
  });

  it("attaches the Idempotency-Key header when provided", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "1" }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/workspaces", {
      method: "POST",
      idempotencyKey: "abc-123",
    });

    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("abc-123");
  });

  it("surfaces a 403 as an ApiError without throwing on missing token", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(403, { error: { code: "forbidden", message: "Not permitted" } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    let caught: unknown;
    try {
      await apiRequest("/api/v1/workspaces/x");
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(403);
    expect((caught as ApiError).code).toBe("forbidden");
  });
});
