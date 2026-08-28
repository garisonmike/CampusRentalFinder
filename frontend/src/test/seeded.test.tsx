import { screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import seeded from "./seeded-platform.json";
import { API } from "./msw/handlers";
import { server } from "./msw/server";
import { renderWithProviders } from "./utils";
import ListingsRoute from "@/app/routes/ListingsRoute";
import PropertyRoute from "@/app/routes/PropertyRoute";
import { HOSTILE_PALETTES } from "@/theme/hostile-palettes";
import { buildTokens } from "@/theme/tokens";

/**
 * The pages, against **real responses from the seeded platform**.
 *
 * `seeded-platform.json` is captured output from `manage.py seed_platform`,
 * not a fixture anybody wrote. That distinction is the whole point: every
 * hand-written fixture in this suite was shaped by the assertion it was made
 * for, so the shapes that break a page are exactly the ones no fixture has.
 * Here the data arrives with the properties it happens to have -- units with
 * no photos, a campus with no walking route, a vacancy nobody has ever
 * stated -- and the page has to survive them.
 *
 * Regenerate by running the seed and re-capturing. If this file starts failing
 * after a backend change, the response shape moved and the page has not.
 */

const listing = seeded.listing as never;
const detail = seeded.detail as never;
const reviews = seeded.reviews as never;
const rating = seeded.rating as never;

function serve() {
  const slug = (seeded.detail as { slug: string }).slug;

  server.use(
    http.get(`${API}/properties/`, () => HttpResponse.json(listing)),
    http.get(`${API}/properties/${slug}/`, () => HttpResponse.json(detail)),
    http.get(`${API}/reviews/properties/${slug}/rating/`, () => HttpResponse.json(rating)),
    http.get(`${API}/reviews/properties/${slug}/`, () => HttpResponse.json(reviews)),
  );
}

function applyPalette(hsl: { primary: string; secondary: string; accent: string }) {
  for (const [name, value] of Object.entries(buildTokens(hsl))) {
    document.documentElement.style.setProperty(name, value);
  }
}

describe("the seeded catalogue is actually varied", () => {
  it("contains the shapes fixtures skip", () => {
    // Asserted on the data, before anything renders it. A capture that
    // silently became eight identical published properties would make every
    // test below pass and prove nothing.
    const rows = (seeded.listing as { results: Array<Record<string, unknown>> }).results;

    expect(rows.length).toBeGreaterThan(4);
    expect(rows.some((row) => row.cover_photo_url === null)).toBe(true);
    expect(new Set(rows.map((row) => row.estate)).size).toBeGreaterThan(1);
  });

  it("has a unit whose vacancy nobody has ever stated", () => {
    const units = (seeded.detail as { units: Array<{ vacancy_freshness: string }> }).units;

    expect(units.some((unit) => unit.vacancy_freshness === "unknown")).toBe(true);
  });

  it("has a campus distance with no walking route", () => {
    const distances = (
      seeded.detail as { campus_distances: Array<{ walking_minutes: number | null }> }
    ).campus_distances;

    expect(distances.some((distance) => distance.walking_minutes === null)).toBe(true);
  });
});

describe("the listing page against real data", () => {
  it("renders every seeded row", async () => {
    serve();

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    const rows = (seeded.listing as { results: Array<{ name: string }> }).results;
    await screen.findByText(rows[0].name);

    // Counted as articles rather than list items: each card contains its own
    // amenity list, so `getAllByRole("listitem")` inside the results list
    // counts the chips too. Real data has amenities; fixtures often do not.
    const list = screen.getByRole("list", { name: "Listings" });
    expect(within(list).getAllByRole("article")).toHaveLength(rows.length);
  });

  it("says 'No photos yet' for the properties that have none", async () => {
    // 18 of the seeded units have no photos at all. A card that rendered a
    // broken image for those would have looked fine against fixtures, which
    // all carry a cover.
    serve();

    renderWithProviders(<ListingsRoute />, { route: "/listings" });

    await screen.findByText(
      (seeded.listing as { results: Array<{ name: string }> }).results[0].name,
    );
    expect(screen.getAllByText("No photos yet").length).toBeGreaterThan(0);
  });
});

describe("the property page against real data", () => {
  it("renders the units, with whatever vacancy state each is in", async () => {
    serve();

    renderWithProviders(<PropertyRoute />, {
      route: `/listings/${(seeded.detail as { slug: string }).slug}`,
      path: "/listings/:slug",
    });

    await screen.findByRole("heading", {
      level: 1,
      name: (seeded.detail as { name: string }).name,
    });

    // The never-stated unit is worded differently from a stale one, and this
    // is the first time that branch has met data it did not choose.
    expect(screen.getAllByText("Never updated").length).toBeGreaterThan(0);
  });

  it("leaves the missing walking route as a dash", async () => {
    serve();

    renderWithProviders(<PropertyRoute />, {
      route: `/listings/${(seeded.detail as { slug: string }).slug}`,
      path: "/listings/:slug",
    });

    expect(await screen.findByText("No walking route yet")).toBeInTheDocument();
    expect(screen.getByText("Not known")).toBeInTheDocument();
  });
});

describe("both real tenant palettes", () => {
  // The two the seed actually creates: the stock green, and JKUAT's
  // low-chroma grey which sits inside the hostile band on purpose.
  it.each(Object.entries(seeded.themes))(
    "renders the listing page under the %s palette",
    async (_name, theme) => {
      applyPalette(theme as { primary: string; secondary: string; accent: string });
      serve();

      const { container } = renderWithProviders(<ListingsRoute />, { route: "/listings" });
      await screen.findByText(
        (seeded.listing as { results: Array<{ name: string }> }).results[0].name,
      );

      expect(await axe(container)).toHaveNoViolations();
    },
  );

  it.each(HOSTILE_PALETTES)(
    "renders the property page under $name with real data",
    async (palette) => {
      applyPalette(palette);
      serve();

      const { container } = renderWithProviders(<PropertyRoute />, {
        route: `/listings/${(seeded.detail as { slug: string }).slug}`,
        path: "/listings/:slug",
      });
      await screen.findByRole("heading", { level: 1 });

      expect(await axe(container)).toHaveNoViolations();
    },
  );
});
