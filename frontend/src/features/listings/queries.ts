import { useQueries, useQuery } from "@tanstack/react-query";

import { get, getPage } from "@/api/client";
import { queryKeys } from "@/api/queries";
import type { Paginated, PropertySummary, Schemas } from "@/api/types";
import { activeFilters, describe, toParams, without, type Filters } from "./filters";

/** One page of search results. */
export function useListings(filters: Filters, page: number) {
  return useQuery({
    queryKey: queryKeys.properties.list({ ...toParams(filters), page }),
    queryFn: () =>
      getPage<PropertySummary>("/properties/", { ...toParams(filters), page }),
  });
}

export interface Blame {
  /** The filter, if exactly one is responsible on its own. */
  key: keyof Filters | null;
  /** What it is excluding, phrased as a fact about the listings. */
  reason: string;
  /** How many listings come back if it is dropped. */
  wouldShow: number;
}

/**
 * Which filter is hiding everything.
 *
 * "No results — try adjusting your filters" is what a site says when it does
 * not know, and it makes the student do a binary search by hand: clear one
 * box, wait for the network, clear another. On a campus connection that is a
 * minute of work to learn something the server could answer in one round trip.
 *
 * So when a search returns nothing, we ask the same question again with each
 * filter dropped in turn, at `page_size=1` -- we want the count, not the
 * listings. Three outcomes, and each says something different:
 *
 * - **exactly one probe finds listings**: that filter is the whole reason.
 *   Name it and say how many it is hiding.
 * - **several do**: no single one is to blame; they are jointly too narrow.
 *   Say so rather than picking the first and being wrong.
 * - **none do**: the search is empty for a deeper reason -- often that this
 *   campus has nothing listed yet, which is a fact about the platform and not
 *   about the student's filters. Never phrase that one as their mistake.
 *
 * The probes run only on an empty result with filters set, so the ordinary
 * path costs nothing.
 */
export function useEmptyReason(filters: Filters, enabled: boolean) {
  const candidates = enabled ? activeFilters(filters) : [];

  const probes = useQueries({
    queries: candidates.map((key) => {
      const relaxed = without(filters, key);
      return {
        queryKey: queryKeys.properties.list({ ...toParams(relaxed), probe: 1 }),
        queryFn: () =>
          getPage<PropertySummary>("/properties/", { ...toParams(relaxed), page_size: 1 }),
        // A probe is a hint, not the page. One failing must never turn an
        // empty-results state into an error state.
        retry: false,
      };
    }),
  });

  const settled = probes.every((probe) => !probe.isPending);
  if (!enabled || candidates.length === 0 || !settled) {
    return { loading: enabled && candidates.length > 0 && !settled, blames: [] as Blame[] };
  }

  const blames: Blame[] = [];

  probes.forEach((probe, index) => {
    const count = (probe.data as Paginated<PropertySummary> | undefined)?.count ?? 0;
    if (count > 0) {
      const key = candidates[index];
      blames.push({ key, reason: describe(key, filters[key]), wouldShow: count });
    }
  });

  // Most restrictive first: the filter hiding the most is the one worth
  // relaxing first, and it is not always the one the student would guess.
  blames.sort((left, right) => right.wouldShow - left.wouldShow);

  return { loading: false, blames };
}

/** One property, by slug. */
export function useProperty(slug: string) {
  return useQuery({
    queryKey: queryKeys.properties.detail(slug),
    queryFn: () => get<Schemas["PropertyDetail"]>(`/properties/${slug}/`),
  });
}

/** One unit, with its own photos. */
export function useUnit(id: number) {
  return useQuery({
    queryKey: queryKeys.units.detail(id),
    queryFn: () => get<Schemas["UnitDetail"]>(`/properties/units/${id}/`),
  });
}

/** The rating figures for one property, plus the landlord's own record. */
export function usePropertyRating(slug: string) {
  return useQuery({
    queryKey: queryKeys.properties.rating(slug),
    queryFn: () => get<Schemas["PropertyRating"]>(`/reviews/properties/${slug}/rating/`),
  });
}

/** One page of reviews for a property. */
export function usePropertyReviews(slug: string, page: number) {
  return useQuery({
    queryKey: queryKeys.properties.reviews(slug, page),
    queryFn: () => getPage<Schemas["Review"]>(`/reviews/properties/${slug}/`, { page }),
  });
}
