import { BadgeCheck, Star } from "lucide-react";

import { count, formatDate } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * One review.
 *
 * **`dispute_annotation` renders as a plain factual line and nothing else.**
 * Not amber, not collapsed, not greyed, not excluded from the average, not
 * behind a "show disputed reviews" toggle. A landlord disputing honestly and
 * one disputing tactically produce the identical annotation — the platform
 * cannot tell them apart and must not pretend to. Styling it as a warning
 * would hand back the veto ADR-004 removed, in CSS instead of in code.
 *
 * The verified badge is shown when present and **its absence says nothing**.
 * Most universities do not require verification (ADR-003), so rendering "not
 * verified" would invent a distinction between students who went through a
 * process their school never asked them to.
 */
export function ReviewCard({ review }: { review: Schemas["Review"] }) {
  return (
    <article className="space-y-3 rounded-lg border border-border p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          {/* "Former student" for an erased account (ADR-008). What they said
              survives; who they were does not. */}
          {review.author_name}
          {review.is_verified_author && (
            <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <BadgeCheck aria-hidden className="size-3.5" />
              Verified student
            </span>
          )}
        </h3>
        <p className="text-xs text-muted-foreground">{formatDate(review.created_at)}</p>
      </header>

      <p className="flex items-center gap-1.5 text-sm">
        <Star aria-hidden className="size-4 fill-current" />
        <span className="font-semibold">{review.rating} out of 5</span>
        <span className="text-muted-foreground">
          · {review.unit_label} · stayed {count(review.stay_months, "month")}
        </span>
      </p>

      {review.comment && (
        <p className="whitespace-pre-line text-sm leading-relaxed">{review.comment}</p>
      )}

      <Aspects review={review} />

      {review.dispute_annotation && (
        // Same type scale, same colour, same weight as any other line on the
        // card. The only thing that marks it is what it says.
        <p className="text-sm text-muted-foreground">{review.dispute_annotation}</p>
      )}

      {review.response && (
        <aside className="rounded-md border-l-2 border-border bg-muted/40 p-3">
          <p className="text-xs font-medium">
            {review.response.author_name} replied · {formatDate(review.response.created_at)}
          </p>
          <p className="mt-1 whitespace-pre-line text-sm">{review.response.body}</p>
        </aside>
      )}
    </article>
  );
}

const ASPECTS: ReadonlyArray<{ key: keyof Schemas["Review"]; label: string }> = [
  { key: "cleanliness_rating", label: "Cleanliness" },
  { key: "security_rating", label: "Security" },
  { key: "water_reliability_rating", label: "Water" },
  { key: "landlord_rating", label: "Landlord" },
  { key: "value_rating", label: "Value" },
];

function Aspects({ review }: { review: Schemas["Review"] }) {
  // Only the ones they answered. A skipped aspect rendered as 0, or as an
  // empty row of stars, converts "did not say" into "said it was terrible".
  const answered = ASPECTS.filter((aspect) => typeof review[aspect.key] === "number");

  if (answered.length === 0) return null;

  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {answered.map((aspect) => (
        <div key={aspect.key} className="flex gap-1">
          <dt className="text-muted-foreground">{aspect.label}</dt>
          <dd className="font-medium">{String(review[aspect.key])}/5</dd>
        </div>
      ))}
    </dl>
  );
}
