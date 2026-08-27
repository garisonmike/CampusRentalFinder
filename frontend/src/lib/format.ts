/**
 * Formatting the API's values for a human, in one place.
 *
 * Two rules, and both are about honesty rather than tidiness.
 *
 * **DRF sends decimals as strings.** `rent_kes: "8500.00"` is a string on
 * purpose — a float would round somebody's rent — and `Number(...)` at forty
 * call sites is forty chances to render `NaN` into a page. It is parsed here.
 *
 * **Null is never rendered as zero.** A missing walking time, an unstated
 * rent, an absent distance: each becomes an em dash, which reads as "we do not
 * know" rather than as a number. A fabricated zero is worse than a gap,
 * because a gap is obvious and a zero is convincing.
 */

/** What every unknown renders as. Never "0", never "N/A", never blank. */
export const UNKNOWN = "—";

/**
 * Kenyan shillings, whole. `KES 8,500`.
 *
 * No cents: rents are quoted in whole shillings and the trailing `.00` is two
 * characters of noise on a 360px screen.
 */
export function formatKes(value: string | number | null | undefined): string {
  const amount = toNumber(value);
  if (amount === null) return UNKNOWN;

  return `KES ${amount.toLocaleString("en-KE", { maximumFractionDigits: 0 })}`;
}

/**
 * Kilometres to one decimal, with the unit.
 *
 * The caller says what kind of distance it is. This deliberately does not
 * append "away" or "to campus": straight-line and walking distances are
 * different facts (`CampusDistance`), and a shared formatter that named one of
 * them would let the wrong label travel with the right number.
 */
export function formatKm(value: string | number | null | undefined): string {
  const km = toNumber(value);
  if (km === null) return UNKNOWN;

  return `${km.toFixed(1)} km`;
}

/** Whole minutes. Null stays null — see `CampusDistance.walking_minutes`. */
export function formatMinutes(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;

  return `${value} min`;
}

/** A date the user can read, in the local calendar. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return UNKNOWN;

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;

  return date.toLocaleDateString("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * "2 days ago", "3 months ago".
 *
 * Coarse on purpose. The precision people want from a timestamp is "recently
 * or not", and an exact hour count invites the reader to treat a rounded
 * server-side band as if it were a live figure.
 */
export function formatAgeInDays(days: number | null | undefined): string {
  if (days === null || days === undefined) return UNKNOWN;
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 31) return `${days} days ago`;

  const months = Math.round(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;

  const years = Math.floor(days / 365);
  return `over ${years} year${years === 1 ? "" : "s"} ago`;
}

/** Pluralise without a library. `count(1, "room")` → "1 room". */
export function count(n: number, noun: string, plural?: string): string {
  return `${n} ${n === 1 ? noun : (plural ?? `${noun}s`)}`;
}

/** Enum value to sentence case: `one_bedroom` → "One bedroom". */
export function humanise(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;

  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
