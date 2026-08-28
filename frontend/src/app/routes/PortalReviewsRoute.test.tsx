import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import PortalReviewsRoute from "./PortalReviewsRoute";
import { API, page, review } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * The landlord's own view of their reviews.
 *
 * The rule under test is the one it would be easiest to quietly break here:
 * a disputed review renders exactly as it does for a student. A landlord who
 * saw theirs faded in their own portal would learn that disputing is how you
 * make a review look less credible.
 */

function signInAs(capabilities: Partial<Schemas["User"]["capabilities"]>) {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 2,
      email: "grace@example.test",
      first_name: "Grace",
      last_name: "Njoroge",
      capabilities: { ...NO_CAPABILITIES, ...capabilities },
    },
  });
}

function serve(reviews: Schemas["Review"][]) {
  server.use(
    http.get(`${API}/reviews/manage/`, ({ request }) => {
      const answered = new URL(request.url).searchParams.get("answered");
      if (answered === "false") {
        return HttpResponse.json(page(reviews.filter((entry) => entry.response === null)));
      }
      return HttpResponse.json(page(reviews));
    }),
  );
}

beforeEach(() => signInAs({ is_landlord: true }));

describe("a disputed review", () => {
  const disputed = review({
    dispute_annotation: "The landlord disputed that this stay took place.",
  });

  it("renders as a plain line in the landlord's own portal too", async () => {
    serve([disputed]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(
      await screen.findByText("The landlord disputed that this stay took place."),
    ).toBeInTheDocument();
  });

  it("is not hidden, collapsed or gated behind a toggle", async () => {
    serve([disputed]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(await screen.findByText(/water goes off most thursdays/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show disputed/i })).not.toBeInTheDocument();
  });

  it("can still be replied to", async () => {
    // Disputing is not an alternative to answering, and a portal that offered
    // the dispute instead of the reply would make it one.
    serve([disputed]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(await screen.findByLabelText(/reply publicly/i)).toBeInTheDocument();
  });
});

describe("the filter", () => {
  it("defaults to what is waiting on you", async () => {
    const answered = review({
      id: 2,
      response: {
        id: 1,
        body: "Fixed in June.",
        author_name: "Grace Njoroge",
        created_at: "2026-07-04T09:00:00Z",
      },
    });
    serve([review({ id: 1 }), answered]);

    renderWithProviders(<PortalReviewsRoute />);

    await screen.findByLabelText(/reply publicly/i);
    expect(screen.queryByText("Fixed in June.")).not.toBeInTheDocument();
  });

  it("shows everything when asked", async () => {
    const answered = review({
      id: 2,
      response: {
        id: 1,
        body: "Fixed in June.",
        author_name: "Grace Njoroge",
        created_at: "2026-07-04T09:00:00Z",
      },
    });
    serve([review({ id: 1 }), answered]);

    renderWithProviders(<PortalReviewsRoute />);

    await userEvent.click(await screen.findByRole("button", { name: "All" }));

    expect(await screen.findByText("Fixed in June.")).toBeInTheDocument();
  });
});

describe("replying", () => {
  it("says the reply is permanent before it is written", async () => {
    serve([review()]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(await screen.findByText(/one reply per review, and it is permanent/i)).toBeInTheDocument();
  });

  it("will not send an empty reply", async () => {
    serve([review()]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(await screen.findByRole("button", { name: /publish reply/i })).toBeDisabled();
  });

  it("posts it and says it is published", async () => {
    serve([review({ id: 100 })]);
    server.use(
      http.post(`${API}/reviews/100/response/`, () => HttpResponse.json({}, { status: 201 })),
    );

    renderWithProviders(<PortalReviewsRoute />);

    await userEvent.type(await screen.findByLabelText(/reply publicly/i), "The tank was replaced.");
    await userEvent.click(screen.getByRole("button", { name: /publish reply/i }));

    expect(await screen.findByText(/your reply is published/i)).toBeInTheDocument();
  });
});

describe("a caretaker", () => {
  it("gets no reply box, and is told why", async () => {
    // Rather than a hidden control they assume is broken. The API enforces
    // this either way (ADR-003); the page explains it.
    signInAs({ is_landlord: false, manages_properties: [1] });
    serve([review()]);

    renderWithProviders(<PortalReviewsRoute />);

    await screen.findByText(/water goes off most thursdays/i);
    expect(screen.queryByLabelText(/reply publicly/i)).not.toBeInTheDocument();
    expect(screen.getByText(/owner's own act/i)).toBeInTheDocument();
  });
});

describe("empty states", () => {
  it("distinguishes 'nothing waiting' from 'no reviews at all'", async () => {
    serve([]);

    renderWithProviders(<PortalReviewsRoute />);

    expect(await screen.findByText(/nothing is waiting on a reply/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "All" }));

    expect(await screen.findByText(/only students with a confirmed tenancy/i)).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve([review()]);

    const { container } = renderWithProviders(<PortalReviewsRoute />);
    await screen.findByText(/water goes off most thursdays/i);

    expect(await axe(container)).toHaveNoViolations();
  });
});
