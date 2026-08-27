import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VacancyNotice, vacancyExplanation } from "./VacancyNotice";
import type { Schemas } from "@/api/types";

/**
 * The vacancy display, tested against the API's contract note rather than
 * against how it looks.
 */

function unit(
  overrides: Partial<Schemas["UnitSummary"]> = {},
): Pick<
  Schemas["UnitSummary"],
  "vacant_count" | "total_count" | "vacancy_freshness" | "vacancy_age_days"
> {
  return {
    vacant_count: 3,
    total_count: 12,
    vacancy_freshness: "fresh",
    vacancy_age_days: 2,
    ...overrides,
  };
}

describe("the count itself", () => {
  it("shows how many are free, out of how many exist", () => {
    render(<VacancyNotice unit={unit()} />);

    expect(screen.getByText(/3 rooms free/)).toBeInTheDocument();
    expect(screen.getByText(/of 12/)).toBeInTheDocument();
  });

  it("still shows a stale count rather than hiding it", () => {
    // The rule from the contract note. Hiding it would replace a number the
    // reader can judge with nothing at all, and they would then assume the
    // flattering answer.
    render(<VacancyNotice unit={unit({ vacancy_freshness: "stale", vacancy_age_days: 120 })} />);

    expect(screen.getByText(/3 rooms free/)).toBeInTheDocument();
  });

  it("never zeroes a stale count", () => {
    render(<VacancyNotice unit={unit({ vacancy_freshness: "stale", vacancy_age_days: 400 })} />);

    expect(screen.queryByText(/None free/)).not.toBeInTheDocument();
  });

  it("says none free rather than 0 rooms free", () => {
    render(<VacancyNotice unit={unit({ vacant_count: 0 })} />);

    expect(screen.getByText(/None free/)).toBeInTheDocument();
  });
});

describe("the band", () => {
  it.each([
    ["fresh", 2, /Updated 2 days ago/],
    ["ageing", 20, /Last updated 20 days ago/],
    ["stale", 120, /Not updated since 4 months ago/],
  ] as const)("words %s in its own terms", (freshness, days, expected) => {
    render(<VacancyNotice unit={unit({ vacancy_freshness: freshness, vacancy_age_days: days })} />);

    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it("distinguishes never-stated from stated-long-ago", () => {
    // Opposite facts about whether anybody has ever looked. Collapsing them
    // would let a listing that has never said anything borrow the wording of
    // one that has.
    render(<VacancyNotice unit={unit({ vacancy_freshness: "unknown", vacancy_age_days: null })} />);

    expect(screen.getByText("Never updated")).toBeInTheDocument();
    expect(screen.queryByText(/ago/)).not.toBeInTheDocument();
  });

  it("renders the server's band even when the age disagrees with it", () => {
    // Deliberately contradictory input: a "fresh" band with a 400-day age.
    // The component must render what the server decided, because the
    // thresholds live in settings and a client that re-derives them owns a
    // second copy that will drift.
    render(<VacancyNotice unit={unit({ vacancy_freshness: "fresh", vacancy_age_days: 400 })} />);

    expect(screen.getByText(/Updated over 1 year ago/)).toBeInTheDocument();
  });
});

describe("the explanation", () => {
  it.each(["fresh", "ageing", "stale", "unknown"] as const)(
    "has words for %s",
    (freshness) => {
      expect(vacancyExplanation(freshness).length).toBeGreaterThan(20);
    },
  );

  it("tells a reader what to do about a stale count", () => {
    // The point of the whole mechanism: not a label, a decision. The failure
    // it prevents is a student paying matatu fare to view a room let in March.
    expect(vacancyExplanation("stale")).toMatch(/ask before you travel/i);
  });
});
