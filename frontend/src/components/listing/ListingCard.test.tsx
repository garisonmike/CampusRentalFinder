import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import { ListingCard } from "./ListingCard";
import { HOSTILE_PALETTES } from "@/theme/hostile-palettes";
import { buildTokens } from "@/theme/tokens";
import type { PropertySummary } from "@/api/types";

/**
 * The card, including under palettes nobody would choose.
 *
 * A card that only reads correctly in the stock green is broken for the second
 * university and for every reader with deuteranopia in every palette. What is
 * machine-checkable about that is asserted here: the structure that carries
 * the hierarchy survives, and every fact has words as well as an icon.
 */

function property(overrides: Partial<PropertySummary> = {}): PropertySummary {
  return {
    id: 1,
    name: "Wendani Court",
    slug: "wendani-court",
    property_type: "hostel_block",
    county: "nairobi",
    town: "Kahawa",
    estate: "Kahawa Wendani",
    landmark: "opposite Naivas",
    latitude: null,
    longitude: null,
    has_water_tank: true,
    has_borehole: false,
    has_backup_power: false,
    has_perimeter_wall: true,
    has_security_guard: true,
    has_wifi: true,
    caretaker_on_site: true,
    published_at: "2026-06-01T09:00:00Z",
    cheapest_rent_kes: "8500.00",
    cover_photo_url: "https://cdn.test/cover.jpg",
    ...overrides,
  };
}

function renderCard(overrides: Partial<PropertySummary> = {}) {
  return render(
    <MemoryRouter>
      <ListingCard property={property(overrides)} />
    </MemoryRouter>,
  );
}

describe("the price", () => {
  it("says 'from', because it is the cheapest unit", () => {
    // Rendering the cheapest rent bare advertises the single room's price for
    // the two-bedroom. It is the oldest trick in property listing and the
    // reason students distrust listing sites.
    renderCard();

    expect(screen.getByText("from")).toBeInTheDocument();
    expect(screen.getByText("KES 8,500")).toBeInTheDocument();
  });

  it("says the rent is not stated rather than showing nothing", () => {
    renderCard({ cheapest_rent_kes: null });

    expect(screen.getByText("Rent not stated")).toBeInTheDocument();
  });
});

describe("the distance", () => {
  it("labels a straight line as a straight line", () => {
    // "1.2 km away" reads as a walk. A walk around a river is not the same
    // journey as a line across it, and the API is explicit that this figure
    // is the line.
    renderCard({ nearest_campus_km: "1.234" });

    expect(screen.getByText("1.2 km")).toBeInTheDocument();
    expect(screen.getByText(/straight line/)).toBeInTheDocument();
  });

  it("shows nothing at all when the list was not ordered by distance", () => {
    // The field is absent rather than null in that case, and absent is the
    // honest render: a dash would imply a measurement that failed.
    renderCard();

    expect(screen.queryByText(/km/)).not.toBeInTheDocument();
  });
});

describe("the cover photo", () => {
  it("says there are none rather than showing a stock image", () => {
    renderCard({ cover_photo_url: null });

    expect(screen.getByText("No photos yet")).toBeInTheDocument();
  });

  it("does not repeat the property name as alt text", () => {
    // The heading is directly below. Alt text repeating it makes a screen
    // reader announce the same name twice for one card.
    const { container } = renderCard();

    expect(container.querySelector("img")).toHaveAttribute("alt", "");
  });
});

describe("amenities", () => {
  it("names each one in words as well as an icon", () => {
    renderCard();

    expect(screen.getByText("Wifi")).toBeInTheDocument();
    expect(screen.getByText("Security guard")).toBeInTheDocument();
  });

  it("omits the ones the property does not have, rather than crossing them out", () => {
    // A greyed-out "no backup power" is the same pixel weight as a real
    // amenity and reads as one at a glance.
    renderCard();

    expect(screen.queryByText("Backup power")).not.toBeInTheDocument();
  });
});

describe("the whole card is the link", () => {
  it("names the link after the property", () => {
    renderCard();

    expect(screen.getByRole("link", { name: "Wendani Court" })).toHaveAttribute(
      "href",
      "/listings/wendani-court",
    );
  });
});

describe("under a hostile palette", () => {
  it.each(HOSTILE_PALETTES)("keeps its structure in $name", async (palette) => {
    // The hierarchy is meant to come from the heading level, the type scale
    // and the ordering -- none of which a tenant can override. If a card
    // stops being legible in navy, it was leaning on the colour.
    const tokens = buildTokens(palette);
    for (const [name, value] of Object.entries(tokens)) {
      document.documentElement.style.setProperty(name, value);
    }

    const { container } = renderCard({ nearest_campus_km: "0.8" });

    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Wendani Court");
    expect(screen.getByText("KES 8,500")).toBeInTheDocument();
    expect(screen.getByText(/straight line/)).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
