import type { Schemas } from "@/api/types";

/**
 * The search filters, as one object with one serialisation.
 *
 * The URL is the state. A student sends a link to a friend, taps back after a
 * listing, or reloads on a flaky connection — all three have to land on the
 * same search. Filters held in component state survive none of them, and
 * filters held in two places (state *and* the URL) disagree the first time
 * somebody uses the back button.
 */

export type UnitType = Schemas["UnitTypeEnum"];
export type Ordering = "distance" | "rent" | "-published_at";

export interface Filters {
  q: string;
  min_rent: string;
  max_rent: string;
  max_distance_km: string;
  unit_type: UnitType[];
  available_only: boolean;
  has_wifi: boolean;
  has_water_tank: boolean;
  has_backup_power: boolean;
  has_security_guard: boolean;
  caretaker_on_site: boolean;
  town: string;
  ordering: Ordering;
}

export const EMPTY_FILTERS: Filters = {
  q: "",
  min_rent: "",
  max_rent: "",
  max_distance_km: "",
  unit_type: [],
  available_only: false,
  has_wifi: false,
  has_water_tank: false,
  has_backup_power: false,
  has_security_guard: false,
  caretaker_on_site: false,
  town: "",
  ordering: "-published_at",
};

/**
 * How each filter is described when it turns out to be the one hiding
 * everything.
 *
 * Written as a sentence about the *listings*, not about the control: "no
 * listing is under KES 6,000" tells a student something true about the market
 * near their campus. "Try adjusting your filters" tells them nothing, and it
 * is what every listing site says when it does not know which one to blame.
 */
const DESCRIPTIONS: {
  [K in keyof Filters]?: (value: Filters[K]) => string;
} = {
  q: (value) => `nothing matches “${value}”`,
  min_rent: (value) => `nothing is above KES ${Number(value).toLocaleString("en-KE")}`,
  max_rent: (value) => `nothing is under KES ${Number(value).toLocaleString("en-KE")}`,
  max_distance_km: (value) => `nothing is within ${value} km of campus`,
  unit_type: (value) =>
    `nothing is ${value.map((type) => type.replace(/_/g, " ")).join(" or ")}`,
  available_only: () => "nothing has a free room right now",
  has_wifi: () => "nothing has wifi",
  has_water_tank: () => "nothing has a water tank",
  has_backup_power: () => "nothing has backup power",
  has_security_guard: () => "nothing has a security guard",
  caretaker_on_site: () => "nothing has a caretaker on site",
  town: (value) => `nothing is in ${value}`,
};

/** Human label for a filter, for chips and for the blame sentence. */
export const LABELS: Record<keyof Filters, string> = {
  q: "search text",
  min_rent: "minimum rent",
  max_rent: "maximum rent",
  max_distance_km: "distance from campus",
  unit_type: "unit type",
  available_only: "available now",
  has_wifi: "wifi",
  has_water_tank: "water tank",
  has_backup_power: "backup power",
  has_security_guard: "security guard",
  caretaker_on_site: "caretaker on site",
  town: "town",
  ordering: "ordering",
};

/** Which filters the user has actually set. `ordering` is never one: it changes
 *  the order of results and can never remove one. */
export function activeFilters(filters: Filters): Array<keyof Filters> {
  return (Object.keys(filters) as Array<keyof Filters>).filter((key) => {
    if (key === "ordering") return false;
    return isSet(filters[key]);
  });
}

/** What a filter is excluding, phrased as a fact about the listings. */
export function describe<K extends keyof Filters>(key: K, value: Filters[K]): string {
  const describer = DESCRIPTIONS[key] as ((value: Filters[K]) => string) | undefined;
  return describer ? describer(value) : `nothing matches ${LABELS[key]}`;
}

/** The same filters with one removed, for a probe query. */
export function without(filters: Filters, key: keyof Filters): Filters {
  return { ...filters, [key]: EMPTY_FILTERS[key] };
}

/**
 * Filters as query parameters.
 *
 * Empty values are dropped rather than sent as `""`, because `?q=` and no `q`
 * at all are the same search and two cache entries for one search means a
 * loading flash every time somebody clears a box.
 */
export function toParams(filters: Filters): Record<string, unknown> {
  const params: Record<string, unknown> = {};

  for (const key of Object.keys(filters) as Array<keyof Filters>) {
    const value = filters[key];
    if (key !== "ordering" && !isSet(value)) continue;
    params[key] = value;
  }

  return params;
}

/** Read filters out of a URL. Unknown values fall back rather than throwing:
 *  a hand-edited or stale link should still render a search. */
export function fromSearchParams(params: URLSearchParams): Filters {
  const ordering = params.get("ordering");

  return {
    ...EMPTY_FILTERS,
    q: params.get("q") ?? "",
    min_rent: numeric(params.get("min_rent")),
    max_rent: numeric(params.get("max_rent")),
    max_distance_km: numeric(params.get("max_distance_km")),
    unit_type: params.getAll("unit_type") as UnitType[],
    available_only: params.get("available_only") === "true",
    has_wifi: params.get("has_wifi") === "true",
    has_water_tank: params.get("has_water_tank") === "true",
    has_backup_power: params.get("has_backup_power") === "true",
    has_security_guard: params.get("has_security_guard") === "true",
    caretaker_on_site: params.get("caretaker_on_site") === "true",
    town: params.get("town") ?? "",
    ordering: isOrdering(ordering) ? ordering : "-published_at",
  };
}

/** Filters as a URL query string, in a stable key order so the same search
 *  always produces the same link. */
export function toSearchParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();

  for (const key of Object.keys(EMPTY_FILTERS) as Array<keyof Filters>) {
    const value = filters[key];

    if (key === "ordering") {
      if (value !== EMPTY_FILTERS.ordering) params.set(key, String(value));
      continue;
    }
    if (!isSet(value)) continue;

    if (Array.isArray(value)) {
      for (const entry of value) params.append(key, entry);
    } else {
      params.set(key, String(value));
    }
  }

  return params;
}

function isSet(value: Filters[keyof Filters]): boolean {
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "boolean") return value;
  return value !== "";
}

/** Non-numeric junk in a URL becomes no filter rather than a 400 from the API. */
function numeric(value: string | null): string {
  if (value === null || value === "") return "";
  return Number.isFinite(Number(value)) ? value : "";
}

function isOrdering(value: string | null): value is Ordering {
  return value === "distance" || value === "rent" || value === "-published_at";
}
