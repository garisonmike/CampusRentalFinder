import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import { ReviewBlock } from "./ReviewBlock";
import { API, NO_REVIEWS, page, propertyRating, ratingAggregate, review } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import type { Schemas } from "@/api/types";

/**
 * The review block, which is where four of the contract notes land at once.
 *
 * Each test below corresponds to a specific way a frontend gets ratings wrong
 * by default, and every one of those defaults is more flattering to the
 * listing than the truth. That is not a coincidence — it is why the notes
 * exist.
 */

function serve({
  rating = propertyRating(),
  reviews = [review()],
}: {
  rating?: Schemas["PropertyRating"];
  reviews?: Schemas["Review"][];
} = {}) {
  server.use(
    http.get(`${API}/reviews/properties/wendani-court/rating/`, () => HttpResponse.json(rating)),
    http.get(`${API}/reviews/properties/wendani-court/`, () => HttpResponse.json(page(reviews))),
  );
}

function renderBlock() {
  return renderWithProviders(<ReviewBlock slug="wendani-court" />);
}

describe("no reviews yet", () => {
  it("says the words, rather than showing a zero", async () => {
    // "0.0 out of 5" and an empty star row are both fabricated signals, and
    // on a trust platform a fabricated signal is worse than none: the reader
    // cannot tell it from a real one.
    // Landlord record emptied too, so the only thing that could produce a
    // score here is the property inventing one.
    serve({
      rating: propertyRating({
        property: NO_REVIEWS,
        landlord: { ...NO_REVIEWS, property_count: 1 },
      }),
      reviews: [],
    });

    renderBlock();

    expect(await screen.findByText("No verified reviews yet")).toBeInTheDocument();
    expect(screen.queryByText(/out of 5/)).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("says an absent rating is not a mark against the listing", async () => {
    serve({ rating: propertyRating({ property: NO_REVIEWS }) });

    renderBlock();

    expect(await screen.findByText(/not a mark against it/i)).toBeInTheDocument();
  });

  it("labels the landlord's record as being about the landlord", async () => {
    // Never as this property's score, and never as a silent fallback for the
    // null above -- that would be the platform answering a question nobody
    // asked, in the listing's favour.
    serve({
      rating: propertyRating({
        property: NO_REVIEWS,
        landlord: { ...ratingAggregate({ average_rating: "4.10" }), property_count: 3 },
      }),
    });

    renderBlock();

    expect(
      await screen.findByText("About this landlord, not this property"),
    ).toBeInTheDocument();
    expect(screen.getByText(/across 3 of their properties/)).toBeInTheDocument();
  });

  it("shows nothing extra when the landlord has no record either", async () => {
    serve({
      rating: propertyRating({ property: NO_REVIEWS, landlord: { ...NO_REVIEWS, property_count: 1 } }),
    });

    renderBlock();

    await screen.findByText("No verified reviews yet");
    expect(screen.queryByText(/about this landlord/i)).not.toBeInTheDocument();
  });
});

describe("the two counts", () => {
  it("uses students as the public denominator", async () => {
    serve({ rating: propertyRating({ property: ratingAggregate({ student_count: 8, review_count: 9 }) }) });

    renderBlock();

    expect(await screen.findByText("8 students")).toBeInTheDocument();
  });

  it("explains the divergence instead of hiding it", async () => {
    // Two numbers that differ with no explanation look like a bug, and the
    // "fix" somebody reaches for is showing the larger one -- which would
    // undo the de-duplication ADR-004 exists for.
    serve({ rating: propertyRating({ property: ratingAggregate({ student_count: 8, review_count: 9 }) }) });

    renderBlock();

    expect(await screen.findByText(/9 reviews in total/)).toBeInTheDocument();
    expect(screen.getByText(/each of them counts once/)).toBeInTheDocument();
  });

  it("says nothing about it when the two agree", async () => {
    serve({ rating: propertyRating({ property: ratingAggregate({ student_count: 8, review_count: 8 }) }) });

    renderBlock();

    await screen.findByText("8 students");
    expect(screen.queryByText(/reviews in total/)).not.toBeInTheDocument();
  });
});

describe("a disputed review", () => {
  const disputed = review({
    dispute_annotation: "The landlord disputed that this stay took place.",
  });

  it("renders the annotation as a plain line", async () => {
    serve({ reviews: [disputed] });

    renderBlock();

    expect(
      await screen.findByText("The landlord disputed that this stay took place."),
    ).toBeInTheDocument();
  });

  it("does not hide, collapse or gate the review behind a toggle", async () => {
    // An honest dispute and a tactical one produce the identical annotation.
    // The platform cannot tell them apart, so anything that reads as a
    // verdict hands back the veto ADR-004 removed -- in CSS instead of code.
    serve({ reviews: [disputed] });

    renderBlock();

    expect(await screen.findByText(/water goes off most thursdays/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show/i })).not.toBeInTheDocument();
  });

  it("still shows its rating, because it is still in the average", async () => {
    serve({ reviews: [disputed] });

    renderBlock();

    expect(await screen.findByText("4 out of 5")).toBeInTheDocument();
  });
});

describe("the author", () => {
  it("badges a verified student", async () => {
    serve({ reviews: [review({ is_verified_author: true })] });

    renderBlock();

    expect(await screen.findByText("Verified student")).toBeInTheDocument();
  });

  it("says nothing at all when they are not verified", async () => {
    // Most universities do not require verification (ADR-003). "Not verified"
    // would invent a distinction between students whose school never asked
    // them to do anything.
    serve({ reviews: [review({ is_verified_author: false })] });

    renderBlock();

    await screen.findByText("4 out of 5");
    expect(screen.queryByText(/verified/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/unverified|not verified/i)).not.toBeInTheDocument();
  });

  it("renders an erased author's name as sent", async () => {
    serve({ reviews: [review({ author_name: "Former student" })] });

    renderBlock();

    expect(await screen.findByText("Former student")).toBeInTheDocument();
  });
});

describe("aspect ratings", () => {
  it("omits the ones the student skipped", async () => {
    // A skipped aspect rendered as 0, or as an empty row of stars, turns
    // "did not say" into "said it was terrible".
    serve({ reviews: [review({ water_reliability_rating: null })] });

    renderBlock();

    await screen.findByText("4 out of 5");
    expect(screen.queryByText("Water")).not.toBeInTheDocument();
    expect(screen.getByText("Cleanliness")).toBeInTheDocument();
  });
});

describe("the landlord's reply", () => {
  it("shows it attributed and dated", async () => {
    serve({
      reviews: [
        review({
          response: {
            id: 1,
            body: "The tank was replaced in June.",
            author_name: "Grace Njoroge",
            created_at: "2026-07-04T09:00:00Z",
          },
        }),
      ],
    });

    renderBlock();

    const card = (await screen.findByText(/water goes off/i)).closest("article");
    expect(within(card as HTMLElement).getByText(/Grace Njoroge replied/)).toBeInTheDocument();
  });
});

describe("failure", () => {
  it("keeps the reviews when the rating request fails", async () => {
    // Two requests because they are two different things. A rating cache
    // that will not load must not take the reviews down with it.
    server.use(
      http.get(`${API}/reviews/properties/wendani-court/rating/`, () =>
        HttpResponse.json({ error: { code: "server_error" } }, { status: 500 }),
      ),
      http.get(`${API}/reviews/properties/wendani-court/`, () =>
        HttpResponse.json(page([review()])),
      ),
    );

    renderBlock();

    expect(await screen.findByText(/water goes off most thursdays/i)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve();

    const { container } = renderBlock();
    await screen.findByText(/water goes off/i);

    expect(await axe(container)).toHaveNoViolations();
  });
});
