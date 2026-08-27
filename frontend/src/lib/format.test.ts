import { describe, expect, it } from "vitest";

import {
  UNKNOWN,
  count,
  formatAgeInDays,
  formatDate,
  formatKes,
  formatKm,
  formatMinutes,
  humanise,
} from "./format";

/**
 * The formatters, and the one rule that matters: nothing invents a number.
 *
 * Most of these cases are the null path, because the null path is the one a
 * component gets wrong. `Number(null)` is 0 and `${null} km` is "null km" —
 * both render as something, so both pass a smoke test and fail a reader.
 */

describe("money", () => {
  it("renders a DRF decimal string", () => {
    expect(formatKes("8500.00")).toBe("KES 8,500");
  });

  it("drops the cents", () => {
    // Rents are quoted whole. The trailing .00 is two characters of noise on
    // a 360px screen and implies a precision nobody uses.
    expect(formatKes("12000.50")).toBe("KES 12,001");
  });

  it.each([null, undefined, ""])("renders %s as unknown, never as zero", (value) => {
    expect(formatKes(value)).toBe(UNKNOWN);
  });

  it("does not render NaN", () => {
    // `Number("about 8000")` is NaN and `KES NaN` is a real thing a page can
    // display, which is why the parse is here and not at the call site.
    expect(formatKes("about 8000")).toBe(UNKNOWN);
  });
});

describe("distance", () => {
  it("renders one decimal", () => {
    expect(formatKm("1.234")).toBe("1.2 km");
  });

  it("renders a missing distance as unknown", () => {
    expect(formatKm(null)).toBe(UNKNOWN);
  });

  it("renders a missing walking time as unknown", () => {
    // The contract note on `walking_minutes` says null must be an em dash and
    // never a zero or the straight-line estimate. This is that, enforced.
    expect(formatMinutes(null)).toBe(UNKNOWN);
  });

  it("renders a real zero-minute walk as zero", () => {
    // The distinction the null rule exists for: nobody said, versus somebody
    // said and the answer was small.
    expect(formatMinutes(0)).toBe("0 min");
  });
});

describe("age", () => {
  it.each([
    [0, "today"],
    [1, "yesterday"],
    [9, "9 days ago"],
    [45, "2 months ago"],
    [400, "over 1 year ago"],
  ])("renders %i days as %s", (days, expected) => {
    expect(formatAgeInDays(days)).toBe(expected);
  });

  it("renders a never-stated age as unknown, not as today", () => {
    // `vacancy_age_days: null` means nobody has ever stated the count. Reading
    // it as 0 would turn "we have never been told" into "told today", which is
    // the exact inversion the API's contract note warns about.
    expect(formatAgeInDays(null)).toBe(UNKNOWN);
  });
});

describe("dates", () => {
  it("renders an ISO timestamp", () => {
    expect(formatDate("2026-03-14T08:00:00Z")).toMatch(/2026/);
  });

  it.each([null, "", "not a date"])("renders %s as unknown", (value) => {
    expect(formatDate(value)).toBe(UNKNOWN);
  });
});

describe("words", () => {
  it("pluralises", () => {
    expect(count(1, "room")).toBe("1 room");
    expect(count(4, "room")).toBe("4 rooms");
  });

  it("takes an irregular plural", () => {
    expect(count(2, "storey", "storeys")).toBe("2 storeys");
  });

  it("humanises an enum value", () => {
    expect(humanise("one_bedroom")).toBe("One bedroom");
  });
});
