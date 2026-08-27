import { Link } from "react-router-dom";
import { Droplets, ShieldCheck, Wifi, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { UNKNOWN, formatKes, formatKm } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { PropertySummary } from "@/api/types";

/**
 * One property in a list.
 *
 * **Hierarchy comes from type, weight and spacing — never from colour.** A
 * university configures its own brand (ADR-005) and the second tenant's is a
 * navy that swallows a primary button or a grey with no chroma at all. If this
 * card only reads correctly in green, it is broken for everyone else and for
 * anyone with deuteranopia in every palette. So: the name is the largest text,
 * the rent is the boldest, everything else is smaller and quieter, and the
 * tenant colour appears at most once.
 *
 * Two numbers here are easy to render dishonestly and both are handled:
 *
 * `nearest_campus_km` is a **straight line**, present only when the list was
 * ordered by distance. Labelled as such — "1.2 km away" reads as a walk, and a
 * walk around a river is not the same journey as a line across it.
 *
 * `cheapest_rent_kes` is the cheapest unit's rent, so it is prefixed "from".
 * Rendering it bare would advertise the single room's price for the
 * two-bedroom, which is the oldest trick in property listing and the reason
 * students distrust listing sites.
 */

type Property = PropertySummary;

const AMENITIES: ReadonlyArray<{
  key: keyof Property;
  label: string;
  Icon: typeof Wifi;
}> = [
  { key: "has_wifi", label: "Wifi", Icon: Wifi },
  { key: "has_backup_power", label: "Backup power", Icon: Zap },
  { key: "has_water_tank", label: "Water tank", Icon: Droplets },
  { key: "has_security_guard", label: "Security guard", Icon: ShieldCheck },
];

export function ListingCard({ property }: { property: Property }) {
  const amenities = AMENITIES.filter((amenity) => property[amenity.key] === true);

  return (
    <article className="group relative flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm transition-shadow hover:shadow-md">
      <Cover property={property} />

      <div className="flex flex-1 flex-col gap-2 p-4">
        <h3 className="text-lg font-semibold leading-snug">
          {/* The whole card is the target: a 44px link inside a card is a
              tap somebody misses on a matatu. The overlay covers the card
              and the heading keeps the accessible name. */}
          <Link
            to={`/listings/${property.slug}`}
            className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {property.name}
          </Link>
        </h3>

        <p className="text-sm text-muted-foreground">
          {[property.estate, property.town].filter(Boolean).join(", ")}
          {property.landmark && (
            <span className="block text-xs">{property.landmark}</span>
          )}
        </p>

        <Distance km={property.nearest_campus_km} />

        {amenities.length > 0 && (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {amenities.map(({ key, label, Icon }) => (
              <li key={key}>
                <Badge>
                  <Icon aria-hidden className="size-3" />
                  {label}
                </Badge>
              </li>
            ))}
          </ul>
        )}

        <p className="mt-auto pt-2 text-base">
          {property.cheapest_rent_kes === null ? (
            <span className="text-muted-foreground">Rent not stated</span>
          ) : (
            <>
              <span className="text-sm text-muted-foreground">from </span>
              <span className="text-lg font-bold">
                {formatKes(property.cheapest_rent_kes)}
              </span>
              <span className="text-sm text-muted-foreground"> a month</span>
            </>
          )}
        </p>
      </div>
    </article>
  );
}

function Cover({ property }: { property: Property }) {
  if (property.cover_photo_url === null) {
    return (
      <div className="flex aspect-[4/3] items-center justify-center border-b border-border bg-muted">
        <p className="text-sm text-muted-foreground">No photos yet</p>
      </div>
    );
  }

  return (
    <img
      src={property.cover_photo_url}
      // The name is already the heading directly below; repeating it here
      // makes a screen reader say it twice for one card.
      alt=""
      loading="lazy"
      decoding="async"
      className="aspect-[4/3] w-full bg-muted object-cover"
    />
  );
}

function Distance({ km }: { km: Property["nearest_campus_km"] }) {
  // Absent, not null, when the list was not ordered by distance -- so the
  // absence is the honest render, not a dash implying a missing measurement.
  if (km === undefined) return null;

  return (
    <p className="text-sm">
      <span className="font-medium">{formatKm(km)}</span>{" "}
      <span className="text-muted-foreground">
        {formatKm(km) === UNKNOWN ? "from campus" : "from campus in a straight line"}
      </span>
    </p>
  );
}

/** The card's own skeleton, so a loading list has the shape of the real one. */
export function ListingCardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("overflow-hidden rounded-lg border border-border bg-card", className)}
      aria-hidden
    >
      <div className="aspect-[4/3] w-full animate-pulse bg-muted" />
      <div className="space-y-2 p-4">
        <div className="h-5 w-3/4 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/2 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/3 animate-pulse rounded bg-muted" />
      </div>
    </div>
  );
}
