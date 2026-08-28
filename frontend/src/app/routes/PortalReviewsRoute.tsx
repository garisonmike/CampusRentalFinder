import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ReviewCard } from "@/components/reviews/ReviewCard";
import { useManagedReviews, useRespondToReview } from "@/features/portal/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * Reviews of the caller's properties, and the one reply each may have.
 *
 * **The annotation rules apply here too.** `ReviewCard` is the same component
 * students see, deliberately: a disputed review renders as a plain factual
 * line in the landlord's own portal, not greyed, not collapsed, not badged. A
 * landlord who saw their disputed reviews faded here would learn that
 * disputing is how you make a review look less credible — which is the veto
 * ADR-004 removed, coming back as a habit instead of as code.
 *
 * A caretaker may read this page and may not reply. The API enforces that; the
 * page says it rather than hiding the box, because a caretaker who cannot see
 * why the reply is missing assumes it is broken.
 */
export default function PortalReviewsRoute() {
  const signedIn = useAuthStore((state) => state.status) === "authenticated";
  const isLandlord = useAuthStore((state) => state.hasRole("landlord"));
  const [filter, setFilter] = useState<"unanswered" | "all">("unanswered");

  const reviews = useManagedReviews(filter === "unanswered" ? false : undefined, signedIn);

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 lg:py-10">
      <header>
        <h1 className="text-2xl font-semibold">Reviews of your properties</h1>
        {!isLandlord && (
          <p className="mt-1 text-sm text-muted-foreground">
            You are a caretaker here, so you can read these but not reply. A public reply
            speaks for the business, which is the owner's own act.
          </p>
        )}
      </header>

      <div role="group" aria-label="Which reviews to show" className="flex gap-2">
        <Button
          variant={filter === "unanswered" ? "default" : "outline"}
          size="sm"
          onClick={() => setFilter("unanswered")}
          aria-pressed={filter === "unanswered"}
        >
          Not yet answered
        </Button>
        <Button
          variant={filter === "all" ? "default" : "outline"}
          size="sm"
          onClick={() => setFilter("all")}
          aria-pressed={filter === "all"}
        >
          All
        </Button>
      </div>

      {reviews.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading reviews…
        </p>
      ) : reviews.isError ? (
        <p role="alert" className="text-sm text-muted-foreground">
          {userFacingMessage(toApiError(reviews.error))}
        </p>
      ) : reviews.data.count === 0 ? (
        <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {filter === "unanswered"
            ? "Nothing is waiting on a reply."
            : "No reviews of your properties yet. Only students with a confirmed tenancy can write one."}
        </p>
      ) : (
        <>
          {/* A real heading, not just a group label. The cards below are h3s
              and a jump from h1 to h3 breaks the outline a screen-reader user
              navigates by -- and naming the current filter is what a sighted
              reader wants here anyway. */}
          <h2 className="text-lg font-semibold">
            {filter === "unanswered"
              ? `Waiting on a reply (${reviews.data.count})`
              : `All reviews (${reviews.data.count})`}
          </h2>
          <ul className="space-y-4">
          {reviews.data.results.map((review) => (
            <li key={review.id} className="space-y-2">
              {/* The student's own component. Same rendering, same rules. */}
              <ReviewCard review={review} />
              {isLandlord && review.response === null && <ReplyBox review={review} />}
            </li>
          ))}
          </ul>
        </>
      )}
    </div>
  );
}

function ReplyBox({ review }: { review: Schemas["Review"] }) {
  const [body, setBody] = useState("");
  const respond = useRespondToReview();
  const inputId = `reply-${review.id}`;

  if (respond.isSuccess) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Your reply is published under this review.
      </p>
    );
  }

  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <label htmlFor={inputId} className="block text-sm font-medium">
        Reply publicly
      </label>
      {/* Said before they write, because it cannot be undone afterwards. */}
      <p id={`${inputId}-rules`} className="text-xs text-muted-foreground">
        One reply per review, and it is permanent — students see it under what they
        wrote. Answering the specific thing usually reads better than disagreeing with
        the rating.
      </p>
      <textarea
        id={inputId}
        aria-describedby={`${inputId}-rules`}
        rows={3}
        maxLength={2000}
        value={body}
        onChange={(event) => setBody(event.target.value)}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
      />
      <Button
        size="sm"
        disabled={body.trim() === "" || respond.isPending}
        onClick={() => respond.mutate({ id: review.id, body })}
      >
        {respond.isPending ? "Publishing…" : "Publish reply"}
      </Button>

      {respond.isError && (
        <p role="alert" className="text-sm font-medium">
          {userFacingMessage(toApiError(respond.error))}
        </p>
      )}
    </div>
  );
}
