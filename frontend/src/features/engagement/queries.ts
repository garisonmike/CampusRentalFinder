import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { del, get, getPage, post } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { Paginated, Schemas } from "@/api/types";

/**
 * Everything a student does with a listing.
 *
 * Mutations invalidate rather than patch the cache. A hand-patched list and a
 * server that disagreed about, say, whether saving twice creates a second row
 * would show the difference until a reload — and the API is explicitly
 * idempotent there, so guessing locally is guessing at a rule the server
 * already knows.
 */

export function useSavedProperties(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.saved.list(),
    queryFn: () => getPage<Schemas["SavedProperty"]>("/engagement/saved/"),
    enabled,
  });
}

export function useSaveProperty() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: Schemas["SavePropertyRequest"]) =>
      post<Schemas["SavedProperty"]>("/engagement/saved/", body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.saved.all() }),
  });
}

export function useUnsaveProperty() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (slug: string) => del(`/engagement/saved/${slug}/`),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.saved.all() }),
  });
}

export function useInquiries(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.inquiries.list(),
    queryFn: () => getPage<Schemas["Inquiry"]>("/engagement/inquiries/"),
    enabled,
  });
}

export function useSendInquiry() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: Schemas["InquiryCreateRequest"]) =>
      post<Schemas["Inquiry"]>("/engagement/inquiries/", body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.inquiries.all() }),
  });
}

export function useApplications(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.applications.list(),
    queryFn: () => getPage<Schemas["Application"]>("/tenancies/applications/"),
    enabled,
  });
}

export function useApply() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (body: Schemas["ApplicationCreateRequest"]) =>
      post<Schemas["Application"]>("/tenancies/applications/", body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.applications.all() }),
  });
}

export function useWithdrawApplication() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (id: number) =>
      post<Schemas["Application"]>(`/tenancies/applications/${id}/withdraw/`),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.applications.all() }),
  });
}

/**
 * The caller's tenancies.
 *
 * `currency` is a **query parameter, not a field**. There is no stored value
 * meaning "current": it is derived from the dates at query time, because a
 * stored flag needs a job to stay true and when the job stops the data lies
 * silently. Filtering by `status=active` would return an empty page rather
 * than an error, which is the worst kind of wrong.
 */
export function useTenancies(currency: "current" | "past" | "upcoming" | undefined, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.tenancies.list(currency),
    queryFn: () =>
      getPage<Schemas["Tenancy"]>("/tenancies/", currency ? { currency } : undefined),
    enabled,
  });
}

/** Is this property in the caller's saved list? Reads the list rather than
 *  asking per property: a student has a handful of saves, not thousands. */
export function isSaved(
  saved: Paginated<Schemas["SavedProperty"]> | undefined,
  slug: string,
): Schemas["SavedProperty"] | undefined {
  return saved?.results.find((entry) => entry.property_slug === slug);
}

export function useUnitRating(id: number) {
  return useQuery({
    queryKey: queryKeys.units.rating(id),
    queryFn: () => get<Schemas["RatingAggregate"]>(`/reviews/units/${id}/rating/`),
  });
}
