import { Link, useParams } from "react-router-dom";
import {
  Camera,
  Cctv,
  Droplets,
  Fence,
  ParkingCircle,
  ShieldCheck,
  UserCheck,
  Waves,
  Wifi,
  Zap,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CampusDistances } from "@/components/listing/CampusDistances";
import { UnitRow } from "@/components/listing/UnitRow";
import { useProperty } from "@/features/listings/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { formatKes, humanise } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * One property.
 *
 * The order is the argument: what it is, where it is, how far the walk really
 * is, then the rooms with their prices and their vacancy provenance, then the
 * landlord. A student deciding whether to spend an hour and a matatu fare on a
 * viewing needs the distance and the vacancy age before they need the
 * amenities, so those come first even though the photographs are prettier.
 */
export default function PropertyRoute() {
  const { slug = "" } = useParams();
  const property = useProperty(slug);

  if (property.isPending) {
    return (
      <Shell>
        <p role="status" className="sr-only">
          Loading this listing…
        </p>
        <div className="h-8 w-2/3 animate-pulse rounded bg-muted" aria-hidden />
        <div className="mt-4 aspect-[16/9] w-full animate-pulse rounded-lg bg-muted" aria-hidden />
      </Shell>
    );
  }

  if (property.isError) {
    const error = toApiError(property.error);

    return (
      <Shell>
        <div role="alert" className="rounded-lg border border-border p-6">
          <h1 className="text-xl font-semibold">
            {error.status === 404 ? "This listing is not available" : "The listing did not load"}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {error.status === 404
              ? "It may have been taken down, or it may belong to another university."
              : userFacingMessage(error)}
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link to="/listings">Back to search</Link>
          </Button>
        </div>
      </Shell>
    );
  }

  const data = property.data;

  return (
    <Shell>
      <header>
        <h1 className="text-2xl font-semibold lg:text-3xl">{data.name}</h1>
        <p className="mt-1 text-muted-foreground">
          {[data.estate, data.town, humanise(data.county)].filter(Boolean).join(", ")}
          {data.landmark && <span className="block text-sm">{data.landmark}</span>}
        </p>
      </header>

      <Cover url={data.cover_photo_url} name={data.name} />

      <section aria-labelledby="distance-heading">
        <h2 id="distance-heading" className="text-lg font-semibold">
          Getting to campus
        </h2>
        <div className="mt-2">
          <CampusDistances distances={data.campus_distances} />
        </div>
      </section>

      <section aria-labelledby="rooms-heading">
        <h2 id="rooms-heading" className="text-lg font-semibold">
          Rooms
        </h2>
        {data.units.length === 0 ? (
          <p className="mt-2 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            The landlord has not listed any rooms here yet. There is nothing to apply for until
            they do.
          </p>
        ) : (
          <>
            <p className="mt-1 text-sm text-muted-foreground">
              {data.cheapest_rent_kes === null
                ? "No rent stated yet."
                : `From ${formatKes(data.cheapest_rent_kes)} a month.`}
            </p>
            <ul className="mt-3 space-y-3">
              {data.units.map((unit) => (
                <li key={unit.id}>
                  <UnitRow unit={unit} propertySlug={data.slug} />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {data.description && (
        <section aria-labelledby="about-heading">
          <h2 id="about-heading" className="text-lg font-semibold">
            About this place
          </h2>
          {/* The landlord's own words, rendered as text. Whitespace preserved
              so their line breaks survive; never as HTML. */}
          <p className="mt-2 whitespace-pre-line text-sm leading-relaxed">{data.description}</p>
        </section>
      )}

      <Amenities property={data} />

      <section aria-labelledby="landlord-heading">
        <h2 id="landlord-heading" className="text-lg font-semibold">
          Listed by
        </h2>
        {/* Reads "Former landlord" for an erased account (ADR-008): the
            listing survives the person, and the page says so plainly rather
            than rendering a blank where a name was. */}
        <p className="mt-1 text-sm">{data.landlord_name}</p>
      </section>
    </Shell>
  );
}

const AMENITIES: ReadonlyArray<{
  key: keyof Schemas["PropertyDetail"];
  label: string;
  Icon: typeof Wifi;
}> = [
  { key: "has_wifi", label: "Wifi", Icon: Wifi },
  { key: "has_water_tank", label: "Water tank", Icon: Droplets },
  { key: "has_borehole", label: "Borehole", Icon: Waves },
  { key: "has_backup_power", label: "Backup power", Icon: Zap },
  { key: "has_security_guard", label: "Security guard", Icon: ShieldCheck },
  { key: "has_perimeter_wall", label: "Perimeter wall", Icon: Fence },
  { key: "has_cctv", label: "CCTV", Icon: Cctv },
  { key: "has_parking", label: "Parking", Icon: ParkingCircle },
  { key: "caretaker_on_site", label: "Caretaker on site", Icon: UserCheck },
];

function Amenities({ property }: { property: Schemas["PropertyDetail"] }) {
  const present = AMENITIES.filter((amenity) => property[amenity.key] === true);

  return (
    <section aria-labelledby="amenities-heading">
      <h2 id="amenities-heading" className="text-lg font-semibold">
        What it has
      </h2>
      {present.length === 0 ? (
        // Not "no amenities" -- the landlord may simply not have filled this
        // in, and asserting absence from an empty form is inventing a fact.
        <p className="mt-2 text-sm text-muted-foreground">
          The landlord has not said what this place has. Worth asking before you view it.
        </p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {present.map(({ key, label, Icon }) => (
            <li key={key}>
              <Badge>
                <Icon aria-hidden className="size-3" />
                {label}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Cover({ url, name }: { url: string | null; name: string }) {
  if (url === null) {
    return (
      <div className="flex aspect-[16/9] flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/50 text-center">
        <Camera aria-hidden className="size-6 text-muted-foreground" />
        <p className="text-sm font-medium">No photos of this property yet</p>
        <p className="max-w-[36ch] text-xs text-muted-foreground">
          Individual rooms may still have photos of their own.
        </p>
      </div>
    );
  }

  return (
    <img
      src={url}
      alt={`${name}, seen from outside`}
      loading="lazy"
      decoding="async"
      className="aspect-[16/9] w-full rounded-lg bg-muted object-cover"
    />
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-3xl space-y-8 px-4 py-6 lg:py-10">{children}</div>;
}
