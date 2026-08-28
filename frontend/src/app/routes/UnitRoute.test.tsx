import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import UnitRoute from "./UnitRoute";
import { API, unitDetail } from "@/test/msw/handlers";
import { server } from "@/test/msw/server";
import { renderWithProviders } from "@/test/utils";

function serve(unit: ReturnType<typeof unitDetail>) {
  server.use(http.get(`${API}/properties/units/10/`, () => HttpResponse.json(unit)));
}

function renderPage() {
  return renderWithProviders(<UnitRoute />, {
    route: "/listings/wendani-court/units/10",
    path: "/listings/:slug/units/:id",
  });
}

describe("what it costs to move in", () => {
  it("puts the deposit beside the rent", async () => {
    serve(unitDetail({ rent_kes: "8500.00", deposit_kes: "17000.00" }));

    renderPage();

    expect(await screen.findByText("KES 17,000")).toBeInTheDocument();
    expect(screen.getByText("First month's rent")).toBeInTheDocument();
  });

  it("says a missing deposit is not stated rather than showing zero", async () => {
    // "KES 0" reads as "no deposit required", which is a claim nobody made
    // and the one a student would most like to believe.
    serve(unitDetail({ deposit_kes: null }));

    renderPage();

    expect(await screen.findByText("Not stated")).toBeInTheDocument();
  });
});

describe("what is included", () => {
  it("states each utility in both directions", async () => {
    // Water listed as included and electricity silently absent reads as an
    // oversight. Token metering is the norm here and a student budgeting for
    // it needs to know which it is.
    serve(unitDetail({ water_included: true, electricity_included: false }));

    renderPage();

    expect(await screen.findByText(/Water:/)).toBeInTheDocument();
    expect(screen.getByText("included")).toBeInTheDocument();
    expect(screen.getAllByText("paid separately").length).toBe(2);
  });
});

describe("vacancy", () => {
  it("answers 'is anything free' with a count, not a yes or no", async () => {
    // A Unit row can be a pool of forty identical rooms, so "available" is a
    // question about a number.
    serve(unitDetail({ vacant_count: 6, total_count: 40 }));

    renderPage();

    expect(await screen.findByText(/6 rooms free/)).toBeInTheDocument();
    expect(screen.getByText(/of 40/)).toBeInTheDocument();
  });

  it("warns when nobody has ever confirmed the count", async () => {
    serve(unitDetail({ vacancy_freshness: "unknown", vacancy_age_days: null }));

    renderPage();

    expect(await screen.findByText("Never updated")).toBeInTheDocument();
    expect(screen.getByText(/never confirmed/i)).toBeInTheDocument();
  });
});

describe("photos", () => {
  it("says there are none rather than showing a placeholder image", async () => {
    serve(unitDetail({ photos: [] }));

    renderPage();

    expect(await screen.findByText("No photos yet")).toBeInTheDocument();
  });
});

describe("accessibility", () => {
  it("has no violations", async () => {
    serve(unitDetail());

    const { container } = renderPage();
    await screen.findByText("Bedsitters");

    expect(await axe(container)).toHaveNoViolations();
  });
});
