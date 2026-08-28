/**
 * The query layer: typed keys, one retry policy, one place pagination is read.
 *
 * Two things are centralised here for the same reason they were centralised on
 * the backend — because a per-page copy is a per-page chance to get it wrong,
 * and getting it wrong is silent.
 *
 * **Keys are built by functions, never by hand.** A hand-written
 * `["properties", filters]` in one component and `["property-list", filters]`
 * in another are two caches for one resource: an invalidation after a write
 * clears one and leaves the other, so the user sees a stale list and a fresh
 * detail of the same thing.
 *
 * **Retry comes from `isRetryable`.** TanStack's default retries everything
 * three times, which on this API means retrying a 400 and a 403 — turning one
 * clear refusal into three and, for an authorisation failure, generating a
 * brute-force signature in somebody's alerting.
 */

import { QueryClient } from "@tanstack/react-query";

import { isRetryable, toApiError } from "@/lib/api-error";

/**
 * Every query key in the application.
 *
 * Hierarchical on purpose: `queryKeys.properties.all` invalidates every
 * property query including detail pages, which is what a write to a property
 * should do. Invalidating a flat key would leave the detail stale.
 */
export const queryKeys = {
  auth: {
    me: () => ["auth", "me"] as const,
  },
  tenant: {
    config: () => ["tenant", "config"] as const,
    policy: () => ["tenant", "policy"] as const,
  },
  properties: {
    all: () => ["properties"] as const,
    list: (filters: Readonly<Record<string, unknown>>) =>
      ["properties", "list", normaliseFilters(filters)] as const,
    detail: (slug: string) => ["properties", "detail", slug] as const,
    rating: (slug: string) => ["properties", "detail", slug, "rating"] as const,
    reviews: (slug: string, page: number) =>
      ["properties", "detail", slug, "reviews", page] as const,
    /** Everything the caller manages, drafts included. Under `properties` so
     *  a write to a property invalidates the management view too. */
    managed: () => ["properties", "managed"] as const,
  },
  reviews: {
    all: () => ["reviews"] as const,
    managed: (answered?: boolean) =>
      ["reviews", "managed", answered ?? "all"] as const,
  },
  units: {
    detail: (id: number) => ["units", "detail", id] as const,
    rating: (id: number) => ["units", "detail", id, "rating"] as const,
  },
  tenancies: {
    all: () => ["tenancies"] as const,
    list: (currency?: string) => ["tenancies", "list", currency ?? "all"] as const,
  },
  applications: {
    all: () => ["applications"] as const,
    list: () => ["applications", "list"] as const,
  },
  claims: {
    all: () => ["claims"] as const,
    list: () => ["claims", "list"] as const,
  },
  inquiries: {
    all: () => ["inquiries"] as const,
    list: () => ["inquiries", "list"] as const,
  },
  saved: {
    all: () => ["saved"] as const,
    list: () => ["saved", "list"] as const,
  },
  verification: {
    all: () => ["verification"] as const,
    mine: () => ["verification", "mine"] as const,
    queue: (status?: string) => ["verification", "queue", status ?? "all"] as const,
  },
} as const;

/**
 * Drop empty filter values before they reach a key.
 *
 * `{ q: "" }` and `{}` are the same search and must share a cache entry.
 * Without this, clearing a text input refetches an identical result set and
 * flashes a loading state at the user for no reason.
 *
 * Sorted, because `{a, b}` and `{b, a}` are the same query and object key
 * order would otherwise make them different cache entries.
 */
function normaliseFilters(
  filters: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(filters)
      .filter(([, value]) => value !== "" && value !== null && value !== undefined)
      .sort(([left], [right]) => left.localeCompare(right)),
  );
}

/**
 * How long TanStack waits before a retry.
 *
 * A throttle is retryable but not *immediately*: hammering a rate limit
 * extends the ban and is indistinguishable from the abuse the limit exists to
 * stop. Exponential from one second, capped so a background refetch cannot
 * sit pending for minutes.
 */
function retryDelay(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30_000);
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Two attempts after the first. Three retries of a 500 is four
        // requests into a struggling server, which is a way of helping it
        // fall over.
        retry: (failureCount, error) => failureCount < 2 && isRetryable(toApiError(error)),
        retryDelay,
        // A listing does not change between a user tapping into a property
        // and tapping back. Refetching on every focus is data the student
        // pays for on a mobile plan.
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
      mutations: {
        // Writes are never retried automatically. A retried POST that
        // actually succeeded the first time creates a second application, a
        // second inquiry, a second claim -- and the API's uniqueness
        // constraints turn that into a 409 the user cannot explain.
        retry: false,
      },
    },
  });
}
