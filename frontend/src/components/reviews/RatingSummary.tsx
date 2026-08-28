import { Star } from "lucide-react";

import { formatDate } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * The rating figures, with the two counts that are supposed to disagree.
 *
 * Four contract notes meet here and each one is a mistake a frontend makes by
 * default:
 *
 * **`average_rating: null` means no verified reviews yet, and must render as
 * those words.** Never 0, never an empty star row, never "—/5". On a platform
 * whose product is trust, a fabricated signal is worse than no signal, because
 * a reader cannot tell it from a real one.
 *
 * **`student_count` is the public denominator**, and it is deliberately
 * smaller than `review_count` whenever somebody reviewed two stays in the same
 * block. That divergence *is* the de-duplication (ADR-004), so "from 8
 * students" and "9 reviews" are both true and both shown; picking the larger
 * number because it flatters the listing would undo the mechanism.
 *
 * **The landlord's record is a separate signal, labelled as such.** A property
 * with no reviews of its own may show it, but never as this property's score
 * and never as a fallback for the null above — that would be the platform
 * quietly answering a question nobody asked.
 */

export function RatingSummary({ rating }: { rating: Schemas["PropertyRating"] }) {
  const property = rating.property;

  return (
    <div className="space-y-4">
      {property.average_rating === null ? (
        <NoReviewsYet landlord={rating.landlord} />
      ) : (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p className="flex items-baseline gap-1.5">
              <Star aria-hidden className="size-5 self-center fill-current" />
              <span className="text-3xl font-bold">{property.average_rating}</span>
              <span className="text-sm text-muted-foreground">out of 5</span>
            </p>
            <p className="text-sm">
              from{" "}
              <strong className="font-semibold">
                {property.student_count === 1
                  ? "1 student"
                  : `${property.student_count} students`}
              </strong>
            </p>
          </div>

          {property.review_count !== property.student_count && (
            // Explained rather than hidden. Two numbers that differ with no
            // explanation look like a bug and invite somebody to "fix" it by
            // showing the bigger one.
            <p className="text-xs text-muted-foreground">
              {property.review_count} reviews in total — some students reviewed more than
              one stay here, and each of them counts once.
            </p>
          )}

          <Distribution
            distribution={property.rating_distribution}
            total={property.review_count}
          />

          {property.last_review_at && (
            <p className="text-xs text-muted-foreground">
              Most recent review {formatDate(property.last_review_at)}.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function NoReviewsYet({ landlord }: { landlord: Schemas["LandlordRating"] }) {
  return (
    <div className="space-y-3">
      {/* The exact words the contract note asks for. */}
      <p className="text-base font-semibold">No verified reviews yet</p>
      <p className="text-sm text-muted-foreground">
        Only students with a confirmed tenancy can review a place, so a new listing has
        none. That is not a mark against it.
      </p>

      {landlord.average_rating !== null && landlord.review_count > 0 && (
        <div className="rounded-lg border border-border p-3">
          {/* Labelled as being about the landlord, never about this property.
              A property's score and its owner's record are different claims
              and merging them would answer a question nobody asked. */}
          <p className="text-sm font-medium">About this landlord, not this property</p>
          <p className="mt-1 text-sm">
            <strong className="font-semibold">{landlord.average_rating} out of 5</strong> from{" "}
            {landlord.student_count === 1 ? "1 student" : `${landlord.student_count} students`}{" "}
            across{" "}
            {landlord.property_count === 1
              ? "their only other property"
              : `${landlord.property_count} of their properties`}
            .
          </p>
        </div>
      )}
    </div>
  );
}

function Distribution({
  distribution,
  total,
}: {
  distribution: Record<string, number>;
  total: number;
}) {
  return (
    <div>
      <h3 className="sr-only">How the scores fall</h3>
      <ul className="space-y-1">
        {[5, 4, 3, 2, 1].map((stars) => {
          const value = distribution[String(stars)] ?? 0;
          const percent = total > 0 ? Math.round((value / total) * 100) : 0;

          return (
            <li key={stars} className="flex items-center gap-2 text-xs">
              <span className="w-12 shrink-0">
                {stars} star{stars === 1 ? "" : "s"}
              </span>
              {/* The bar is decoration; the count beside it is the fact. A
                  chart whose only reading is its length is unreadable to
                  anyone the length is not rendered for. */}
              <span aria-hidden className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
                <span
                  className="block h-full rounded-full bg-foreground/70"
                  style={{ width: `${percent}%` }}
                />
              </span>
              <span className="w-8 shrink-0 text-right tabular-nums">{value}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
