import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ListingCard, ListingCardSkeleton } from "@/components/listing/ListingCard";
import { FilterPanel } from "@/components/search/FilterPanel";
import { NoResults } from "@/components/search/NoResults";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import {
  EMPTY_FILTERS,
  activeFilters,
  fromSearchParams,
  toSearchParams,
  type Filters,
} from "@/features/listings/filters";
import { useEmptyReason, useListings } from "@/features/listings/queries";
import { useTenant } from "@/theme/TenantThemeProvider";

/**
 * Search.
 *
 * The URL holds the filters, so a link is a search: sending one to a friend,
 * tapping back from a listing and reloading on a flaky connection all land in
 * the same place. Page number is deliberately *not* in the URL — a stale link
 * to page 4 of a list that has since gained listings points at different
 * properties than it did, which is worse than starting at the top.
 */
export default function ListingsRoute() {
  const { config } = useTenant();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);

  const filters = useMemo(() => fromSearchParams(searchParams), [searchParams]);

  const apply = useCallback(
    (next: Filters) => {
      setPage(1);
      // Replace, not push: a back button that walks through every keystroke
      // is a back button nobody can use to leave the page.
      setSearchParams(toSearchParams(next), { replace: true });
    },
    [setSearchParams],
  );

  const listings = useListings(filters, page);
  const isEmpty = !listings.isPending && !listings.isError && listings.data?.count === 0;
  const { blames, loading: diagnosing } = useEmptyReason(filters, isEmpty);

  const results = listings.data?.results ?? [];

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 lg:py-10">
      <h1 className="text-2xl font-semibold lg:text-3xl">
        Rooms near {config?.display_name ?? "campus"}
      </h1>

      <div className="mt-6 grid gap-6 lg:grid-cols-[18rem_1fr]">
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <details open className="rounded-lg border border-border p-4 lg:open:border-0 lg:p-0">
            <summary className="cursor-pointer text-sm font-medium lg:hidden">Filters</summary>
            <div className="pt-4 lg:pt-0">
              <FilterPanel
                filters={filters}
                onChange={apply}
                onClear={(key) => apply({ ...filters, [key]: EMPTY_FILTERS[key] })}
                onClearAll={() => apply(EMPTY_FILTERS)}
              />
            </div>
          </details>
        </aside>

        <section aria-labelledby="results-heading" className="min-w-0">
          {/* A real heading, not just an aria-label: the cards below are h3s
              and a jump from h1 to h3 breaks the outline a screen-reader user
              navigates by. Hidden because the h1 above already says it. */}
          <h2 id="results-heading" className="sr-only">
            Search results
          </h2>
          {listings.isPending ? (
            <>
              {/* Announced, not just drawn: a skeleton is invisible to a
                  screen reader and the page would otherwise be silent. */}
              <p className="sr-only" role="status">
                Loading listings…
              </p>
              <div className="grid gap-4 sm:grid-cols-2">
                {Array.from({ length: 4 }, (_, index) => (
                  <ListingCardSkeleton key={index} />
                ))}
              </div>
            </>
          ) : listings.isError ? (
            <ErrorState error={listings.error} onRetry={() => void listings.refetch()} />
          ) : isEmpty ? (
            <NoResults
              filters={filters}
              blames={blames}
              loading={diagnosing}
              hasFilters={activeFilters(filters).length > 0}
              onClear={(key) => apply({ ...filters, [key]: EMPTY_FILTERS[key] })}
              onClearAll={() => apply(EMPTY_FILTERS)}
            />
          ) : (
            <>
              <p className="mb-4 text-sm text-muted-foreground" role="status">
                {listings.data.count === 1 ? "1 listing" : `${listings.data.count} listings`}
              </p>
              <ul className="grid gap-4 sm:grid-cols-2">
                {results.map((property) => (
                  <li key={property.id}>
                    <ListingCard property={property} />
                  </li>
                ))}
              </ul>

              {listings.data.total_pages > 1 && (
                <nav aria-label="Pages" className="mt-6 flex items-center justify-between gap-4">
                  <Button
                    variant="outline"
                    disabled={!listings.data.previous}
                    onClick={() => setPage((current) => current - 1)}
                  >
                    Previous
                  </Button>
                  <p className="text-sm text-muted-foreground">
                    Page {listings.data.page} of {listings.data.total_pages}
                  </p>
                  <Button
                    variant="outline"
                    disabled={!listings.data.next}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    Next
                  </Button>
                </nav>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-border p-6"
    >
      <h2 className="text-lg font-semibold">The listings did not load</h2>
      {/* The API's message, mapped through the error client -- never the raw
          `detail` string and never a stack trace. */}
      <p className="text-sm text-muted-foreground">{userFacingMessage(toApiError(error))}</p>
      <Button onClick={onRetry}>Try again</Button>
    </div>
  );
}
