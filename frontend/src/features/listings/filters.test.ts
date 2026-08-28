import { describe, expect, it } from "vitest";

import {
  EMPTY_FILTERS,
  activeFilters,
  describe as describeFilter,
  fromSearchParams,
  toParams,
  toSearchParams,
  without,
} from "./filters";

/**
 * The filter model. The URL is the state, so round-tripping is the property
 * that matters: a link somebody sends must reproduce their search exactly.
 */

describe("the URL round trip", () => {
  it("survives a full set of filters", () => {
    const filters = {
      ...EMPTY_FILTERS,
      q: "kahawa",
      max_rent: "9000",
      unit_type: ["bedsitter", "single_room"] as const,
      has_wifi: true,
      ordering: "rent" as const,
    };

    expect(fromSearchParams(toSearchParams({ ...filters, unit_type: [...filters.unit_type] })))
      .toEqual({ ...filters, unit_type: [...filters.unit_type] });
  });

  it("leaves an empty search as an empty URL", () => {
    // `?q=&max_rent=` and no query string are the same search. Two cache
    // entries for one search means a loading flash every time somebody
    // clears a box.
    expect(toSearchParams(EMPTY_FILTERS).toString()).toBe("");
  });

  it("keeps a stable key order, so the same search is the same link", () => {
    const one = toSearchParams({ ...EMPTY_FILTERS, q: "a", has_wifi: true });
    const two = toSearchParams({ ...EMPTY_FILTERS, has_wifi: true, q: "a" });

    expect(one.toString()).toBe(two.toString());
  });

  it("ignores junk in a hand-edited link rather than sending it to the API", () => {
    // A stale or edited link should still render a search. Passing
    // `max_rent=cheap` through would earn a 400 and an error page for what
    // is really just a bad bookmark.
    const filters = fromSearchParams(new URLSearchParams("max_rent=cheap&ordering=sideways"));

    expect(filters.max_rent).toBe("");
    expect(filters.ordering).toBe("-published_at");
  });
});

describe("what counts as a filter", () => {
  it("does not count ordering", () => {
    // Ordering changes the order of results. It can never remove one, so
    // blaming it for an empty page would be a lie.
    expect(activeFilters({ ...EMPTY_FILTERS, ordering: "rent" })).toEqual([]);
  });

  it("counts a set box and ignores an empty one", () => {
    expect(activeFilters({ ...EMPTY_FILTERS, q: "", max_rent: "9000" })).toEqual(["max_rent"]);
  });

  it("counts a checkbox only when it is on", () => {
    expect(activeFilters({ ...EMPTY_FILTERS, has_wifi: false })).toEqual([]);
    expect(activeFilters({ ...EMPTY_FILTERS, has_wifi: true })).toEqual(["has_wifi"]);
  });
});

describe("the parameters sent to the API", () => {
  it("drops empty values", () => {
    expect(toParams({ ...EMPTY_FILTERS, q: "kahawa" })).toEqual({
      q: "kahawa",
      ordering: "-published_at",
    });
  });
});

describe("dropping one filter", () => {
  it("resets it to its empty value, not to undefined", () => {
    const relaxed = without({ ...EMPTY_FILTERS, unit_type: ["bedsitter"] }, "unit_type");

    expect(relaxed.unit_type).toEqual([]);
  });
});

describe("the blame wording", () => {
  it("describes the listings, not the control", () => {
    // "Nothing is under KES 6,000" tells a student something true about the
    // market near their campus. "Adjust the max rent filter" tells them
    // nothing they did not already know.
    expect(describeFilter("max_rent", "6000")).toBe("nothing is under KES 6,000");
  });

  it("has words for every filter", () => {
    for (const key of activeFilters({
      ...EMPTY_FILTERS,
      q: "x",
      min_rent: "1",
      max_rent: "2",
      max_distance_km: "3",
      unit_type: ["bedsitter"],
      available_only: true,
      has_wifi: true,
      has_water_tank: true,
      has_backup_power: true,
      has_security_guard: true,
      caretaker_on_site: true,
      town: "Kahawa",
    })) {
      const filters = { ...EMPTY_FILTERS, [key]: key === "unit_type" ? ["bedsitter"] : "1" };
      expect(describeFilter(key, filters[key]).length).toBeGreaterThan(5);
    }
  });
});
