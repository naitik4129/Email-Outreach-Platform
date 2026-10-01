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

  it("carries the server's structured details on an ApiError", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(422, {
        error: {
          code: "website_thin",
          message: "Not enough to read",
          details: { company_name: "Acme" },
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const error = (await apiRequest("/x", { method: "POST" }).catch((e) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.details).toEqual({ company_name: "Acme" });
  });

  it.each([
    ["no details", { error: { code: "x", message: "m" } }],
    ["null details", { error: { code: "x", message: "m", details: null } }],
    ["a list of details", { error: { code: "x", message: "m", details: [1, 2] } }],
    ["a string", { error: { code: "x", message: "m", details: "oops" } }],
  ])("reports null details for %s", async (_label, body) => {
    getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(400, body)));
    const error = (await apiRequest("/x", { method: "POST" }).catch((e) => e)) as ApiError;
    expect(error.details).toBeNull();
  });

  it("keeps constructing an ApiError the old way (details default to null)", () => {
    expect(new ApiError("m", 500, "c", "r").details).toBeNull();
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

  describe("transient failures", () => {
    // Retry delays are real timers; make them instant so the suite stays fast.
    function instantTimers() {
      vi.spyOn(globalThis, "setTimeout").mockImplementation(((fn: () => void) => {
        fn();
        return 0;
      }) as unknown as typeof setTimeout);
    }

    it("retries a GET on 503 and then succeeds", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse(503, { error: { code: "service_unavailable", message: "busy" } }, {
            "Retry-After": "2",
          }),
        )
        .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      const result = await apiRequest("/api/v1/workspaces");

      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(result.data).toEqual({ ok: true });
    });

    it("retries a GET on a network error and then succeeds", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi
        .fn()
        .mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
      vi.stubGlobal("fetch", fetchMock);

      const result = await apiRequest("/api/v1/workspaces");

      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(result.data).toEqual({ ok: true });
    });

    it("gives up on a GET after two retries and surfaces the server error", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi.fn().mockImplementation(async () =>
        jsonResponse(
          500,
          { error: { code: "internal_error", message: "Internal server error" } },
          { "x-request-id": "rid-1" },
        ),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiRequest("/api/v1/workspaces")).rejects.toMatchObject({
        status: 500,
        message: "Internal server error",
        requestId: "rid-1",
      });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("reports a persistent network failure as an ApiError, not a raw TypeError", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiRequest("/api/v1/workspaces")).rejects.toMatchObject({
        name: "ApiError",
        status: 0,
        code: "network_error",
      });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });

    it("never retries a write, even on 503", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi.fn().mockImplementation(async () =>
        jsonResponse(503, { error: { code: "service_unavailable", message: "busy" } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(
        apiRequest("/api/v1/workspaces", { method: "POST", body: "{}" }),
      ).rejects.toMatchObject({ status: 503 });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("does not retry a 4xx", async () => {
      instantTimers();
      getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
      const fetchMock = vi.fn().mockImplementation(async () =>
        jsonResponse(404, { error: { code: "not_found", message: "Mailbox not found" } }),
      );
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiRequest("/api/v1/mailboxes/x")).rejects.toMatchObject({ status: 404 });
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
  });
});
