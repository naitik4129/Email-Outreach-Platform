import { afterEach, describe, expect, it, vi } from "vitest";

const { getSession, refreshSession, signOut } = vi.hoisted(() => ({
  getSession: vi.fn(),
  refreshSession: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("@/lib/supabase/client", () => ({
  clearLocalAuthSession: signOut,
  createClient: () => ({
    auth: { getSession, refreshSession, signOut },
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
    getSession.mockReset();
    refreshSession.mockReset();
    signOut.mockReset();
  });

  it("attaches the current access token as a bearer credential", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "token-123" } } });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/me");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer token-123");
  });

  it("labels JSON requests as JSON but leaves multipart uploads to the browser", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "token-123" } } });
    // A fresh Response per call: a body can only be read once.
    const fetchMock = vi.fn().mockImplementation(async () => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest("/api/v1/json", { method: "POST", body: JSON.stringify({ a: 1 }) });
    const form = new FormData();
    form.append("file", new File(["x"], "a.pdf"));
    await apiRequest("/api/v1/upload", { method: "POST", body: form });

    expect(fetchMock.mock.calls[0][1].headers["Content-Type"]).toBe("application/json");
    // The browser must add the multipart boundary itself; a JSON label would break it.
    expect(fetchMock.mock.calls[1][1].headers["Content-Type"]).toBeUndefined();
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer token-123");
    expect(fetchMock.mock.calls[1][1].body).toBe(form);
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

  it("retries via refresh on 401 even when there was no initial token", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    refreshSession.mockResolvedValue({
      data: { session: { access_token: "fresh" } },
      error: null,
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(401, { error: { code: "unauthenticated", message: "no session" } }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest("/api/v1/me");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
    expect(fetchMock.mock.calls[1][1].headers.Authorization).toBe("Bearer fresh");
    expect(result.data).toEqual({ ok: true });
  });

  it("shares a single in-flight refresh across concurrent 401s", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "expired" } } });
    let resolveRefresh!: (value: {
      data: { session: { access_token: string } | null };
      error: null;
    }) => void;
    refreshSession.mockReturnValue(
      new Promise((resolve) => {
        resolveRefresh = resolve;
      }),
    );
    const fetchMock = vi.fn().mockImplementation((url, init) => {
      const auth = init.headers.Authorization;
      if (auth === "Bearer fresh") return Promise.resolve(jsonResponse(200, { ok: true }));
      return Promise.resolve(
        jsonResponse(401, { error: { code: "unauthenticated", message: "expired" } }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = apiRequest("/api/v1/me");
    const second = apiRequest("/api/v1/workspaces");
    await Promise.resolve();
    await Promise.resolve();
    resolveRefresh({ data: { session: { access_token: "fresh" } }, error: null });

    const [firstResult, secondResult] = await Promise.all([first, second]);

    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(firstResult.data).toEqual({ ok: true });
    expect(secondResult.data).toEqual({ ok: true });
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
    await vi.waitFor(() => expect(signOut).toHaveBeenCalled());
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
