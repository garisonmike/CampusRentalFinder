import { describe, expect, it } from "vitest";

import { createQueryClient, queryKeys } from "./queries";

/**
 * The query layer.
 *
 * The bugs these guard against are all silent: a stale list beside a fresh
 * detail, a loading flash on an identical search, a retried POST that creates
 * a second application.
 */

describe("query keys", () => {
  it("nests detail under the resource so one invalidation clears both", () => {
    // A flat key would leave the detail stale after a write to the list, and
    // the user would see a property that no longer matches its own card.
    const all = queryKeys.properties.all();
    const detail = queryKeys.properties.detail("wendani-block-a");

    expect(detail.slice(0, all.length)).toEqual([...all]);
  });

  it("nests a property's reviews and rating under its detail", () => {
    const detail = queryKeys.properties.detail("wendani-block-a");

    expect(queryKeys.properties.rating("wendani-block-a").slice(0, detail.length)).toEqual([
      ...detail,
    ]);
    expect(queryKeys.properties.reviews("wendani-block-a", 1).slice(0, detail.length)).toEqual([
      ...detail,
    ]);
  });

  it("treats an empty filter as no filter", () => {
    // Clearing a text input must not refetch an identical result set and
    // flash a loading state.
    expect(queryKeys.properties.list({ q: "" })).toEqual(queryKeys.properties.list({}));
    expect(queryKeys.properties.list({ q: null })).toEqual(queryKeys.properties.list({}));
    expect(queryKeys.properties.list({ q: undefined })).toEqual(queryKeys.properties.list({}));
  });

  it("does not treat a meaningful falsy value as empty", () => {
    // `has_borehole=false` is a filter. Dropping it would silently widen the
    // search to include boreholes the user excluded.
    expect(queryKeys.properties.list({ has_borehole: false })).not.toEqual(
      queryKeys.properties.list({}),
    );
    expect(queryKeys.properties.list({ max_rent: 0 })).not.toEqual(
      queryKeys.properties.list({}),
    );
  });

  it("is insensitive to filter order", () => {
    // `{a, b}` and `{b, a}` are the same query. Object key order would
    // otherwise make them two cache entries and two network calls.
    expect(queryKeys.properties.list({ town: "Nairobi", max_rent: 9000 })).toEqual(
      queryKeys.properties.list({ max_rent: 9000, town: "Nairobi" }),
    );
  });

  it("distinguishes tenancy currencies", () => {
    // Current and past tenancies are different result sets behind one URL.
    expect(queryKeys.tenancies.list("current")).not.toEqual(
      queryKeys.tenancies.list("past"),
    );
  });
});

describe("retry policy", () => {
  const client = createQueryClient();
  const retry = client.getDefaultOptions().queries?.retry;

  /** An axios-shaped rejection. TanStack types the argument as `Error`, but
   *  what actually arrives is whatever the query function threw. */
  function rejection(status: number, code: string): Error {
    return Object.assign(new Error(code), {
      response: {
        status,
        data: { error: { code, message: "", field_errors: {}, request_id: "" } },
      },
    });
  }

  function shouldRetry(status: number, code: string): boolean {
    return typeof retry === "function" ? Boolean(retry(0, rejection(status, code))) : false;
  }

  it("retries a server error", () => {
    expect(shouldRetry(500, "server_error")).toBe(true);
  });

  it("does not retry a validation failure", () => {
    // TanStack's default retries everything three times, which on this API
    // means retrying a payload the server has already explained is wrong.
    expect(shouldRetry(400, "validation_failed")).toBe(false);
  });

  it("does not retry a permission denial", () => {
    // Retrying an authorisation failure is how a client generates its own
    // brute-force signature in somebody's alerting.
    expect(shouldRetry(403, "permission_denied")).toBe(false);
  });

  it("does not retry a conflict", () => {
    expect(shouldRetry(409, "conflict")).toBe(false);
  });

  it("gives up after two retries", () => {
    expect(
      typeof retry === "function" && retry(2, rejection(500, "server_error")),
    ).toBeFalsy();
  });

  it("never retries a mutation", () => {
    // A retried POST that actually succeeded creates a second application, a
    // second inquiry, a second claim -- and the API's uniqueness constraints
    // turn that into a 409 the user cannot explain.
    expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  });

  it("backs off before retrying a throttle", () => {
    // Hammering a rate limit extends the ban and is indistinguishable from
    // the abuse the limit exists to stop.
    const delay = client.getDefaultOptions().queries?.retryDelay;

    expect(typeof delay === "function" && delay(0, new Error())).toBeGreaterThanOrEqual(1000);
  });

  it("does not refetch on window focus", () => {
    // Data the student pays for on a mobile plan, for a listing that did not
    // change while they read a message.
    expect(client.getDefaultOptions().queries?.refetchOnWindowFocus).toBe(false);
  });
});
