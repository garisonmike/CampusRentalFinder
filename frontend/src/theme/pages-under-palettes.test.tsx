import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { beforeEach, describe, expect, it } from "vitest";

import { HOSTILE_PALETTES } from "./hostile-palettes";
import { buildTokens } from "./tokens";
import DashboardRoute from "@/app/routes/DashboardRoute";
import ListingsRoute from "@/app/routes/ListingsRoute";
import PortalReviewsRoute from "@/app/routes/PortalReviewsRoute";
import PortalRoute from "@/app/routes/PortalRoute";
import PropertyRoute from "@/app/routes/PropertyRoute";
import SavedRoute from "@/app/routes/SavedRoute";
import UnitRoute from "@/app/routes/UnitRoute";
import VacancyRoute from "@/app/routes/VacancyRoute";
import {
  API,
  page,
  propertyDetail,
  propertyRating,
  propertySummary,
  review,
  unitDetail,
  unitSummary,
} from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { NO_CAPABILITIES, useAuthStore } from "@/stores/auth";
import { renderWithProviders } from "@/test/utils";

/**
 * Every real page, under every hostile palette.
 *
 * **What this can and cannot prove.** jsdom has no layout and no painting, so
 * axe's contrast rule does not run here and this suite cannot tell you that a
 * button vanished into a navy background. `theme/contrast.test.ts` covers that
 * mathematically across ~30k colours, and the screenshot pass covers it for
 * human eyes.
 *
 * What this proves is the thing those two miss: that each page's **meaning
 * survives without colour**. If a heading stops being a heading, if a control
 * loses its accessible name, if a fact is only conveyed by a coloured chip,
 * that is a page whose hierarchy was being carried by the palette — and it is
 * equally broken for a reader with deuteranopia in *every* palette, including
 * the pretty one it was designed in.
 *
 * Named rather than looped over a spread, so a failure says which page and
 * which palette.
 */

function applyPalette(palette: (typeof HOSTILE_PALETTES)[number]): void {
  for (const [name, value] of Object.entries(buildTokens(palette))) {
    document.documentElement.style.setProperty(name, value);
  }
}

function signIn() {
  useAuthStore.setState({
    status: "authenticated",
    user: {
      id: 1,
      email: "wanjiku@students.ku.ac.ke",
      first_name: "Wanjiku",
      last_name: "Kamau",
      capabilities: { ...NO_CAPABILITIES, is_student: true, is_landlord: true },
    },
  });
}

beforeEach(() => {
  signIn();
  server.use(
    http.get(`${API}/properties/`, () => HttpResponse.json(page([propertySummary()]))),
    http.get(`${API}/properties/wendani-court/`, () => HttpResponse.json(propertyDetail())),
    http.get(`${API}/properties/units/10/`, () => HttpResponse.json(unitDetail())),
    http.get(`${API}/reviews/properties/wendani-court/rating/`, () =>
      HttpResponse.json(propertyRating()),
    ),
    http.get(`${API}/reviews/properties/wendani-court/`, () =>
      HttpResponse.json(page([review()])),
    ),
    http.get(`${API}/engagement/saved/`, () => HttpResponse.json(page([]))),
    http.get(`${API}/engagement/inquiries/`, () => HttpResponse.json(page([]))),
    http.get(`${API}/tenancies/`, () => HttpResponse.json(page([]))),
    http.get(`${API}/tenancies/applications/`, () => HttpResponse.json(page([]))),
    http.get(`${API}/tenancies/claims/`, () => HttpResponse.json(page([]))),
    http.get(`${API}/properties/manage/`, () =>
      HttpResponse.json([
        propertyDetail({ units: [unitSummary({ vacancy_freshness: "stale", vacancy_age_days: 90 })] }),
      ]),
    ),
    http.get(`${API}/reviews/manage/`, () => HttpResponse.json(page([review()]))),
  );
});

/** Each page, with the thing that must still be readable after it loads. */
const PAGES = [
  {
    name: "search",
    render: () => renderWithProviders(<ListingsRoute />, { route: "/listings" }),
    settled: () => screen.findByText("Wendani Court"),
    /** Facts that must survive as words, not as a colour or a position. */
    facts: ["Wendani Court", "KES 8,500"],
  },
  {
    name: "property detail",
    render: () =>
      renderWithProviders(<PropertyRoute />, {
        route: "/listings/wendani-court",
        path: "/listings/:slug",
      }),
    settled: () => screen.findByRole("heading", { level: 1, name: "Wendani Court" }),
    facts: ["Getting to campus", "as the crow flies", "Listed by"],
  },
  {
    name: "unit detail",
    render: () =>
      renderWithProviders(<UnitRoute />, {
        route: "/listings/wendani-court/units/10",
        path: "/listings/:slug/units/:id",
      }),
    settled: () => screen.findByRole("heading", { level: 1, name: "Bedsitters" }),
    facts: ["What it costs to move in", "Is anything free?"],
  },
  {
    name: "saved listings",
    render: () => renderWithProviders(<SavedRoute />),
    settled: () => screen.findByText("Nothing saved yet"),
    facts: ["Nothing saved yet"],
  },
  {
    name: "student dashboard",
    render: () => renderWithProviders(<DashboardRoute />),
    settled: () => screen.findByText(/no current tenancy on record/i),
    facts: ["Where you live now", "Applications"],
  },
  {
    name: "vacancy counts",
    render: () => renderWithProviders(<VacancyRoute />),
    settled: () => screen.findByRole("heading", { name: /worth updating first/i }),
    facts: ["Worth updating first", "Rooms free of"],
  },
  {
    name: "portal reviews",
    render: () => renderWithProviders(<PortalReviewsRoute />),
    settled: () => screen.findByText(/water goes off/i),
    facts: ["Reviews of your properties", "Reply publicly"],
  },
  {
    name: "landlord portal",
    render: () => renderWithProviders(<PortalRoute />),
    settled: () => screen.findByText(/silence is a signal/i),
    facts: ["Stays waiting on you", "Applications to decide"],
  },
] as const;

describe.each(PAGES)("$name", ({ render, settled, facts }) => {
  it.each(HOSTILE_PALETTES)("keeps its meaning in $name", async (palette) => {
    applyPalette(palette);

    const { container } = render();
    await settled();

    // The outline a screen-reader user navigates by. A page that reorders or
    // drops it under one palette was leaning on the palette.
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();

    for (const fact of facts) {
      expect(
        screen.getAllByText(new RegExp(fact.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i")).length,
      ).toBeGreaterThan(0);
    }

    expect(await axe(container)).toHaveNoViolations();
  });
});
