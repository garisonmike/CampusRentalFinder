import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  LABELS,
  activeFilters,
  describe,
  type Filters,
  type Ordering,
  type UnitType,
} from "@/features/listings/filters";
import { cn } from "@/lib/utils";

/**
 * The filters, as plain form controls.
 *
 * No custom select, no popover, no range slider. A native `<select>` opens as
 * the platform's own picker on Android, works with one thumb on a moving
 * matatu, and costs nothing to download; a slider is impossible to set
 * precisely on a small screen and unusable with a keyboard without work that
 * would exceed the budget for the whole panel.
 *
 * Every control has a visible label. Placeholder-as-label disappears the
 * moment somebody types, which is exactly when they most need to know what box
 * they are in.
 *
 * Applied filters are also shown as removable chips. The panel collapses on a
 * phone, and a filter you cannot see is a filter you forget you set — which is
 * how somebody concludes there is nothing near their campus when they have a
 * 6,000-shilling cap from last term still applied.
 */

const UNIT_TYPES: ReadonlyArray<{ value: UnitType; label: string }> = [
  { value: "bedsitter", label: "Bedsitter" },
  { value: "single_room", label: "Single room" },
  { value: "one_bedroom", label: "One bedroom" },
  { value: "two_bedroom", label: "Two bedroom" },
  { value: "three_bedroom", label: "Three bedroom" },
  { value: "hostel_block", label: "Hostel block" },
  { value: "shared_house", label: "Shared house" },
  { value: "maisonette", label: "Maisonette" },
];

const AMENITIES: ReadonlyArray<{ key: keyof Filters; label: string }> = [
  { key: "available_only", label: "Has a free room now" },
  { key: "has_wifi", label: "Wifi" },
  { key: "has_water_tank", label: "Water tank" },
  { key: "has_backup_power", label: "Backup power" },
  { key: "has_security_guard", label: "Security guard" },
  { key: "caretaker_on_site", label: "Caretaker on site" },
];

const ORDERINGS: ReadonlyArray<{ value: Ordering; label: string }> = [
  { value: "-published_at", label: "Newest first" },
  { value: "rent", label: "Cheapest first" },
  { value: "distance", label: "Nearest campus first" },
];

interface Props {
  filters: Filters;
  onChange: (filters: Filters) => void;
  onClear: (key: keyof Filters) => void;
  onClearAll: () => void;
  className?: string;
}

export function FilterPanel({ filters, onChange, onClear, onClearAll, className }: Props) {
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    onChange({ ...filters, [key]: value });

  const applied = activeFilters(filters);

  return (
    <form
      role="search"
      aria-label="Filter listings"
      onSubmit={(event) => event.preventDefault()}
      className={cn("space-y-5", className)}
    >
      <Field label="Search" htmlFor="filter-q">
        <input
          id="filter-q"
          type="search"
          value={filters.q}
          onChange={(event) => set("q", event.target.value)}
          placeholder="Estate, landmark, property name"
          className={inputClass}
        />
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Monthly rent (KES)</legend>
        <div className="flex items-center gap-2">
          <input
            aria-label="Minimum rent"
            type="number"
            inputMode="numeric"
            min={0}
            value={filters.min_rent}
            onChange={(event) => set("min_rent", event.target.value)}
            placeholder="Min"
            className={inputClass}
          />
          <span aria-hidden className="text-muted-foreground">
            –
          </span>
          <input
            aria-label="Maximum rent"
            type="number"
            inputMode="numeric"
            min={0}
            value={filters.max_rent}
            onChange={(event) => set("max_rent", event.target.value)}
            placeholder="Max"
            className={inputClass}
          />
        </div>
      </fieldset>

      <Field label="Within (km of campus)" htmlFor="filter-distance">
        <input
          id="filter-distance"
          type="number"
          inputMode="decimal"
          min={0}
          step={0.5}
          value={filters.max_distance_km}
          onChange={(event) => set("max_distance_km", event.target.value)}
          className={inputClass}
        />
        {/* The API is explicit that this is the straight line, and the
            difference matters where a river or a railway is in the way. */}
        <p className="mt-1 text-xs text-muted-foreground">
          Measured in a straight line, not walking distance.
        </p>
      </Field>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Unit type</legend>
        <ul className="space-y-1.5">
          {UNIT_TYPES.map((type) => (
            <li key={type.value}>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={filters.unit_type.includes(type.value)}
                  onChange={(event) =>
                    set(
                      "unit_type",
                      event.target.checked
                        ? [...filters.unit_type, type.value]
                        : filters.unit_type.filter((entry) => entry !== type.value),
                    )
                  }
                  className="size-4 rounded border-input"
                />
                {type.label}
              </label>
            </li>
          ))}
        </ul>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Must have</legend>
        <ul className="space-y-1.5">
          {AMENITIES.map((amenity) => (
            <li key={amenity.key}>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={filters[amenity.key] === true}
                  onChange={(event) =>
                    onChange({ ...filters, [amenity.key]: event.target.checked })
                  }
                  className="size-4 rounded border-input"
                />
                {amenity.label}
              </label>
            </li>
          ))}
        </ul>
      </fieldset>

      <Field label="Sort by" htmlFor="filter-ordering">
        <select
          id="filter-ordering"
          value={filters.ordering}
          onChange={(event) => set("ordering", event.target.value as Ordering)}
          className={inputClass}
        >
          {ORDERINGS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>

      {applied.length > 0 && (
        <div className="space-y-2 border-t border-border pt-4">
          <p className="text-sm font-medium">
            {applied.length === 1 ? "1 filter applied" : `${applied.length} filters applied`}
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {applied.map((key) => (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => onClear(key)}
                  // The chip says what it will do, not just what it is: "wifi"
                  // as a button label leaves a screen reader user guessing
                  // whether pressing it sets or clears the filter.
                  aria-label={`Remove the ${LABELS[key]} filter`}
                  title={describe(key, filters[key])}
                  className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Badge variant="brand">
                    {LABELS[key]}
                    <X aria-hidden className="size-3" />
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
          <Button type="button" variant="ghost" size="sm" onClick={onClearAll}>
            Clear all
          </Button>
        </div>
      )}
    </form>
  );
}

const inputClass =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium">
        {label}
      </label>
      {children}
    </div>
  );
}
