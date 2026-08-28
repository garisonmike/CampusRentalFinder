import { UNKNOWN, formatKm, formatMinutes } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * How far this property is from each campus — both numbers, never merged.
 *
 * `straight_line_km` is a line on a map. `walking_distance_km` and
 * `walking_minutes` come from a routing provider and are **legitimately
 * null**: no route, out of quota, or the provider is down. The API will never
 * substitute the straight line for the walk, and neither will this.
 *
 * The temptation is obvious — a table with a gap in it looks unfinished, and
 * the straight line is *right there*. But a 1.2 km line across the Nairobi
 * river is a 40-minute walk, and a student who budgets 15 minutes for their
 * 8 a.m. lecture on the strength of a number we made up will not make that
 * mistake twice, nor trust anything else on the page. An em dash is the whole
 * fix: it says we do not know, which is true, and it is the one thing a
 * fabricated number can never be.
 */
export function CampusDistances({
  distances,
}: {
  distances: readonly Schemas["CampusDistance"][];
}) {
  if (distances.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        This property is not yet linked to a campus, so we cannot say how far it is.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Distance to each campus</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-2 pr-4 font-medium">
              Campus
            </th>
            <th scope="col" className="py-2 pr-4 font-medium">
              Straight line
            </th>
            <th scope="col" className="py-2 font-medium">
              Walking
            </th>
          </tr>
        </thead>
        <tbody>
          {distances.map((distance) => (
            <tr key={`${distance.university_name}-${distance.campus_name}`} className="border-b border-border/60">
              <th scope="row" className="py-2 pr-4 text-left font-normal">
                {distance.campus_name}
                <span className="block text-xs text-muted-foreground">
                  {distance.university_name}
                </span>
              </th>
              <td className="py-2 pr-4">
                {formatKm(distance.straight_line_km)}
                <span className="block text-xs text-muted-foreground">as the crow flies</span>
              </td>
              <td className="py-2">
                <Walk distance={distance} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Walk({ distance }: { distance: Schemas["CampusDistance"] }) {
  const minutes = formatMinutes(distance.walking_minutes);

  if (minutes === UNKNOWN) {
    return (
      <>
        <span aria-hidden>{UNKNOWN}</span>
        {/* The dash alone is silence to a screen reader, and silence in a
            table cell reads as an empty value rather than a missing one. */}
        <span className="sr-only">Not known</span>
        <span className="block text-xs text-muted-foreground">
          No walking route yet
        </span>
      </>
    );
  }

  return (
    <>
      {minutes}
      <span className="block text-xs text-muted-foreground">
        {formatKm(distance.walking_distance_km)} on foot
      </span>
    </>
  );
}
