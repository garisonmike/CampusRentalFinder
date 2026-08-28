import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import PropertyRoute from "./PropertyRoute";
import {
  API,
  NO_REVIEWS,
  campusDistance,
  page,
  propertyDetail,
  propertyRating,
  unitSummary,
} from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";

/**
 * The property page.
 *
 * Most of these are about the two figures a listing page can render
 * dishonestly without anybody noticing: the walk to campus, and how long ago
 * anyone confirmed a room was free.
 */

function serve(property: ReturnType<typeof propertyDetail>) {
  server.use(
    http.get(`${API}/properties/wendani-court/`, () => HttpResponse.json(property)),
    // The page carries the review block, which fetches its own two things.
    // Stubbed empty here so these tests are about the property rather than
    // about the ratings, and so nothing reaches the network unhandled.
    http.get(`${API}/reviews/properties/wendani-court/rating/`, () =>
      HttpResponse.json(propertyRating({ property: NO_REVIEWS, landlord: { ...NO_REVIEWS, property_count: 0 } })),
    ),
    http.get(`${API}/reviews/properties/wendani-court/`, () => HttpResponse.json(page([]))),
  );
}

function renderPage() {
  return renderWithProviders(<PropertyRoute />, {
    route: "/listings/wendani-court",
    path: "/listings/:slug",
  });
}

describe("distance to campus", () => {
  it("never substitutes the straight line for a missing walk", async () => {
    // The straight line is right there and the table looks unfinished
    // without it. But a 1.2 km line across the river is a 40-minute walk,
    // and a student who budgets 15 minutes on the strength of a number we
    // invented will not trust anything else on the page.
    serve(propertyDetail({ campus_distances: [campusDistance()] }));

    renderPage();

    expect(await screen.findByText("as the crow flies")).toBeInTheDocument();
    expect(screen.getByText("No walking route yet")).toBeInTheDocument();
    expect(screen.queryByText(/1.2 km on foot/)).not.toBeInTheDocument();
  });

  it("says 'not known' to a screen reader rather than reading out a dash", async () => {
    // An em dash alone is silence, and silence in a table cell reads as an
    // empty value rather than a missing one.
    serve(propertyDetail({ campus_distances: [campusDistance()] }));

    renderPage();

    expect(await screen.findByText("Not known")).toBeInTheDocument();
  });

  it("shows both numbers when the walk is known", async () => {
    serve(
      propertyDetail({
        campus_distances: [
          campusDistance({ walking_minutes: 18, walking_distance_km: "1.60" }),
        ],
      }),
    );

    renderPage();

    expect(await screen.findByText("18 min")).toBeInTheDocument();
    expect(screen.getByText("1.6 km on foot")).toBeInTheDocument();
    expect(screen.getByText("1.2 km")).toBeInTheDocument();
  });

  it("says so when the property is linked to no campus at all", async () => {
    serve(propertyDetail({ campus_distances: [] }));

    renderPage();

    expect(await screen.findByText(/not yet linked to a campus/i)).toBeInTheDocument();
  });
});

describe("rooms", () => {
  it("shows the vacancy count with its age", async () => {
    serve(
      propertyDetail({
        units: [unitSummary({ vacancy_freshness: "stale", vacancy_age_days: 120 })],
      }),
    );

    renderPage();

    expect(await screen.findByText(/6 rooms free/)).toBeInTheDocument();
    expect(screen.getByText(/not updated since 4 months ago/i)).toBeInTheDocument();
    expect(screen.getByText(/ask before you travel/i)).toBeInTheDocument();
  });

  it("shows the deposit beside the rent", async () => {
    // A student comparing two listings at 8,500 is really comparing 8,500
    // and 17,000 on move-in day.
    serve(propertyDetail());

    renderPage();

    expect(await screen.findByText("Deposit")).toBeInTheDocument();
    expect(screen.getAllByText("KES 8,500").length).toBeGreaterThan(0);
  });

  it("says there is nothing to apply for when no rooms are listed", async () => {
    serve(propertyDetail({ units: [], cheapest_rent_kes: null }));

    renderPage();

    expect(await screen.findByText(/has not listed any rooms/i)).toBeInTheDocument();
  });
});

describe("amenities", () => {
  it("does not claim a place has nothing when the landlord left the form blank", async () => {
    // "No amenities" asserts a fact about the building. An empty form is a
    // fact about the form.
    serve(
      propertyDetail({
        has_wifi: false,
        has_water_tank: false,
        has_borehole: false,
        has_backup_power: false,
        has_security_guard: false,
        has_perimeter_wall: false,
        has_cctv: false,
        has_parking: false,
        caretaker_on_site: false,
      }),
    );

    renderPage();

    expect(await screen.findByText(/has not said what this place has/i)).toBeInTheDocument();
  });
});

describe("the landlord", () => {
  it("renders the erased-account name as sent, rather than as a blank", async () => {
    // ADR-008: the listing survives the person. A blank where a name was
    // looks like a bug and invites somebody to "fix" it by hiding the field.
    serve(propertyDetail({ landlord_name: "Former landlord" }));

    renderPage();

    expect(await screen.findByText("Former landlord")).toBeInTheDocument();
  });
});

describe("when it is gone", () => {
  it("says the listing is unavailable rather than showing an error code", async () => {
    server.use(
      http.get(`${API}/properties/wendani-court/`, () =>
        HttpResponse.json({ error: { code: "not_found" } }, { status: 404 }),
      ),
    );

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent(/not available/i);
    expect(screen.getByRole("link", { name: /back to search/i })).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve(propertyDetail());

    const { container } = renderPage();
    await screen.findByText("Wendani Court");

    expect(await axe(container)).toHaveNoViolations();
  });
});
