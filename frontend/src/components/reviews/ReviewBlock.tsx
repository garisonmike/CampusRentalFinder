import { useState } from "react";

import { Button } from "@/components/ui/button";
import { RatingSummary } from "@/components/reviews/RatingSummary";
import { ReviewCard } from "@/components/reviews/ReviewCard";
import { usePropertyRating, usePropertyReviews } from "@/features/listings/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";

/**
 * Ratings and reviews for one property.
 *
 * The figures and the reviews are two requests because they are two different
 * things: the aggregate is a cache rebuilt by a job (ADR-004) and the list is
 * paginated. Failing separately is the point — a rating that will not load
 * must not take the reviews down with it, and vice versa.
 */
export function ReviewBlock({ slug }: { slug: string }) {
  const [page, setPage] = useState(1);
  const rating = usePropertyRating(slug);
  const reviews = usePropertyReviews(slug, page);

  return (
    <section aria-labelledby="reviews-heading" className="space-y-4">
      <h2 id="reviews-heading" className="text-lg font-semibold">
        What students said
      </h2>

      {rating.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading ratings…
        </p>
      ) : rating.isError ? (
        <p role="alert" className="text-sm text-muted-foreground">
          {userFacingMessage(toApiError(rating.error))}
        </p>
      ) : (
        <RatingSummary rating={rating.data} />
      )}

      {reviews.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading reviews…
        </p>
      ) : reviews.isError ? (
        <p role="alert" className="text-sm text-muted-foreground">
          {userFacingMessage(toApiError(reviews.error))}
        </p>
      ) : reviews.data.count === 0 ? (
        // Deliberately not a second "no reviews yet" -- the summary above has
        // already said it, and repeating it makes the page look emptier than
        // the listing is.
        null
      ) : (
        <>
          <ul className="space-y-3">
            {reviews.data.results.map((review) => (
              <li key={review.id}>
                <ReviewCard review={review} />
              </li>
            ))}
          </ul>

          {reviews.data.total_pages > 1 && (
            <nav aria-label="Review pages" className="flex items-center justify-between gap-4">
              <Button
                variant="outline"
                size="sm"
                disabled={!reviews.data.previous}
                onClick={() => setPage((current) => current - 1)}
              >
                Previous
              </Button>
              <p className="text-sm text-muted-foreground">
                Page {reviews.data.page} of {reviews.data.total_pages}
              </p>
              <Button
                variant="outline"
                size="sm"
                disabled={!reviews.data.next}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </Button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
