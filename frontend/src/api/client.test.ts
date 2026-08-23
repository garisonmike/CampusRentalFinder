import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";

import { get, refreshAccessToken, setSessionExpiredHandler } from "./client";
import { clearTokens, getAccessToken, setTokens } from "./tokens";

/**
 * The refresh interceptor is the piece most likely to fail quietly in
 * production and hardest to debug from a bug report, so it is tested
 * directly rather than through a component.
 */
describe("refresh on 401", () => {
  beforeEach(() => {
    clearTokens();
    setSessionExpiredHandler(() => {});
  });

  it("refreshes once for N concurrent 401s and replays every request", async () => {
    setTokens({ access: "expired", refresh: "refresh-1" });

    let refreshCalls = 0;
    let issued = 0;

    server.use(
      http.post(`${API}/auth/token/refresh/`, async () => {
        refreshCalls += 1;
        // Hold the response open so all six requests queue behind this one.
        await new Promise((resolve) => setTimeout(resolve, 20));
        return HttpResponse.json({ access: `fresh-${++issued}` });
      }),
      http.get(`${API}/protected/`, ({ request }) => {
        const auth = request.headers.get("authorization");
        if (auth === "Bearer expired") {
          return HttpResponse.json({ detail: "Token expired" }, { status: 401 });
        }
        return HttpResponse.json({ ok: true, auth });
      }),
    );

    const results = await Promise.all(
      Array.from({ length: 6 }, () => get<{ ok: boolean; auth: string }>("/protected/")),
    );

    // The bug this prevents: one refresh per concurrent 401. With
    // ROTATE_REFRESH_TOKENS on, the first rotation invalidates the rest and
    // the user is logged out mid-session.
    expect(refreshCalls).toBe(1);
    expect(results).toHaveLength(6);
    for (const result of results) {
      expect(result.ok).toBe(true);
      expect(result.auth).toBe("Bearer fresh-1");
    }
  });

  it("stores the rotated refresh token when the server sends one", async () => {
    setTokens({ access: "expired", refresh: "refresh-1" });

    server.use(
      http.post(`${API}/auth/token/refresh/`, () =>
        HttpResponse.json({ access: "fresh", refresh: "refresh-2" }),
      ),
    );

    await refreshAccessToken();

    expect(getAccessToken()).toBe("fresh");
  });

  it("retries a failed request only once", async () => {
    setTokens({ access: "expired", refresh: "refresh-1" });

    let attempts = 0;
    server.use(
      http.post(`${API}/auth/token/refresh/`, () => HttpResponse.json({ access: "still-bad" })),
      http.get(`${API}/protected/`, () => {
        attempts += 1;
        return HttpResponse.json({ detail: "nope" }, { status: 401 });
      }),
    );

    await expect(get("/protected/")).rejects.toThrow();

    // Original plus exactly one retry. Without the flag this recurses.
    expect(attempts).toBe(2);
  });

  it("clears the session and notifies when the refresh itself fails", async () => {
    setTokens({ access: "expired", refresh: "revoked" });
    const onExpired = vi.fn();
    setSessionExpiredHandler(onExpired);

    server.use(
      http.post(`${API}/auth/token/refresh/`, () =>
        HttpResponse.json({ detail: "Token is blacklisted" }, { status: 401 }),
      ),
      http.get(`${API}/protected/`, () => HttpResponse.json({ detail: "nope" }, { status: 401 })),
    );

    await expect(get("/protected/")).rejects.toThrow();

    expect(getAccessToken()).toBeNull();
    expect(onExpired).toHaveBeenCalledOnce();
  });

  it("does not attempt a refresh when there is no refresh credential", async () => {
    let refreshCalls = 0;
    server.use(
      http.post(`${API}/auth/token/refresh/`, () => {
        refreshCalls += 1;
        return HttpResponse.json({ access: "fresh" });
      }),
      http.get(`${API}/protected/`, () => HttpResponse.json({ detail: "nope" }, { status: 401 })),
    );

    await expect(get("/protected/")).rejects.toThrow();

    expect(refreshCalls).toBe(0);
  });

  it("leaves non-401 failures alone", async () => {
    setTokens({ access: "valid", refresh: "refresh-1" });

    let refreshCalls = 0;
    server.use(
      http.post(`${API}/auth/token/refresh/`, () => {
        refreshCalls += 1;
        return HttpResponse.json({ access: "fresh" });
      }),
      http.get(`${API}/protected/`, () =>
        HttpResponse.json({ detail: "Server error" }, { status: 500 }),
      ),
    );

    await expect(get("/protected/")).rejects.toThrow();

    expect(refreshCalls).toBe(0);
  });
});

describe("pagination", () => {
  it("returns the DRF envelope rather than a bare array", async () => {
    server.use(
      http.get(`${API}/things/`, () =>
        HttpResponse.json({
          count: 2,
          next: null,
          previous: null,
          results: [{ id: 1 }, { id: 2 }],
        }),
      ),
    );

    const { getPage } = await import("./client");
    const page = await getPage<{ id: number }>("/things/");

    // The previous client typed this as an array and called .map() on it,
    // which is why no list view ever rendered.
    expect(Array.isArray(page)).toBe(false);
    expect(page.count).toBe(2);
    expect(page.results).toHaveLength(2);
  });
});
