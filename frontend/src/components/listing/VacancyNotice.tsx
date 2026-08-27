import { Badge } from "@/components/ui/badge";
import { formatAgeInDays, count } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * What the landlord said was free, and when they said it.
 *
 * `vacant_count` is stated by the landlord and never derived — they know about
 * the room let off-platform last week and we do not. The cost of that choice
 * is that the number is only as good as its age, so the API ships
 * `vacancy_freshness` and `vacancy_age_days` beside it and the contract note
 * says: **surface this, and never present a stale count as current.**
 *
 * Three rules follow, and each is a test below:
 *
 * **The count is always shown.** Never hidden while stale, never zeroed. A
 * number the reader can judge beats no number, and a zero asserts something
 * nobody said.
 *
 * **The band comes from the server.** `fresh | ageing | stale | unknown` is
 * computed from thresholds in settings. Re-deriving it from `vacancy_age_days`
 * here would be a second copy of those thresholds, and `docs/OPERATIONS.md`
 * has five entries about which copy wins.
 *
 * **"Never stated" is not "stated long ago".** They are opposite facts about
 * whether anyone has ever looked, and they are worded differently.
 */

type Unit = Pick<
  Schemas["UnitSummary"],
  "vacant_count" | "total_count" | "vacancy_freshness" | "vacancy_age_days"
>;

/** Wording per band. The words carry the meaning; the styling only supports it. */
const FRESHNESS: Record<
  Schemas["VacancyFreshnessEnum"],
  { label: (age: string) => string; variant: "neutral" | "note" }
> = {
  fresh: { label: (age) => `Updated ${age}`, variant: "neutral" },
  ageing: { label: (age) => `Last updated ${age}`, variant: "neutral" },
  // "note", not a warning colour: a stale count is not a bad landlord, and
  // amber would read as an accusation the platform cannot support.
  stale: { label: (age) => `Not updated since ${age}`, variant: "note" },
  unknown: { label: () => "Never updated", variant: "note" },
};

export function VacancyNotice({ unit }: { unit: Unit }) {
  const band = FRESHNESS[unit.vacancy_freshness];
  const age = formatAgeInDays(unit.vacancy_age_days);

  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <span className="font-semibold text-foreground">
        {unit.vacant_count === 0
          ? "None free"
          : `${count(unit.vacant_count, "room")} free`}
        <span className="font-normal text-muted-foreground">
          {" "}
          of {unit.total_count}
        </span>
      </span>

      <Badge variant={band.variant}>{band.label(age)}</Badge>
    </p>
  );
}

/**
 * The sentence a detail page shows under the count.
 *
 * Exported separately because a card has room for a badge and a detail page
 * has room for the reason, and the reason is the part that actually stops
 * somebody travelling across Nairobi to see a room let in March.
 */
export function vacancyExplanation(freshness: Schemas["VacancyFreshnessEnum"]): string {
  switch (freshness) {
    case "fresh":
      return "The landlord confirmed this recently.";
    case "ageing":
      return "It has been a while since the landlord confirmed this. Ask before you travel.";
    case "stale":
      return "Nobody has confirmed this in a long time. It may already be taken — ask before you travel.";
    case "unknown":
      return "The landlord has never confirmed how many rooms are free here. Treat the number as a guess.";
  }
}
