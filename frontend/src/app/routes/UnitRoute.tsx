import { Link, useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PhotoGallery } from "@/components/listing/PhotoGallery";
import { VacancyNotice, vacancyExplanation } from "@/components/listing/VacancyNotice";
import { useUnit } from "@/features/listings/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { count, formatDate, formatKes, humanise } from "@/lib/format";

/**
 * One unit, with its own photos.
 *
 * The page a student actually decides from, so the two numbers that decide it
 * lead: what it costs to move in, and how long ago anybody confirmed a room is
 * free. Both are stated rather than implied — `is_available` is a derived
 * boolean over a count and a pool of forty rooms is never simply "available".
 */
export default function UnitRoute() {
  const { slug = "", id = "" } = useParams();
  const unit = useUnit(Number(id));

  if (unit.isPending) {
    return (
      <Shell>
        <p role="status" className="sr-only">
          Loading this room…
        </p>
        <div className="aspect-[4/3] w-full animate-pulse rounded-lg bg-muted" aria-hidden />
      </Shell>
    );
  }

  if (unit.isError) {
    const error = toApiError(unit.error);

    return (
      <Shell>
        <div role="alert" className="rounded-lg border border-border p-6">
          <h1 className="text-xl font-semibold">
            {error.status === 404 ? "This room is not available" : "The room did not load"}
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {error.status === 404
              ? "It may have been taken down, or the listing may belong to another university."
              : userFacingMessage(error)}
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link to={`/listings/${slug}`}>Back to the property</Link>
          </Button>
        </div>
      </Shell>
    );
  }

  const data = unit.data;

  return (
    <Shell>
      <nav aria-label="Breadcrumb" className="text-sm">
        <Link to={`/listings/${slug}`} className="text-muted-foreground underline-offset-4 hover:underline">
          {data.property_name}
        </Link>
      </nav>

      <header>
        <h1 className="text-2xl font-semibold">{data.label}</h1>
        <p className="mt-1 text-2xl font-bold">
          {formatKes(data.rent_kes)}
          <span className="text-base font-normal text-muted-foreground"> a month</span>
        </p>
      </header>

      <PhotoGallery photos={data.photos} label={`Photos of ${data.label}`} />

      <section aria-labelledby="cost-heading" className="rounded-lg border border-border p-4">
        <h2 id="cost-heading" className="text-lg font-semibold">
          What it costs to move in
        </h2>
        {/* Together, because they are paid together. A listing that mentions
            the deposit only after the viewing is one that wasted the fare. */}
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <div>
            <dt className="text-muted-foreground">First month's rent</dt>
            <dd className="font-medium">{formatKes(data.rent_kes)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Deposit</dt>
            <dd className="font-medium">
              {data.deposit_kes === null ? "Not stated" : formatKes(data.deposit_kes)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Minimum stay</dt>
            <dd className="font-medium">{count(data.min_stay_months, "month")}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Available from</dt>
            <dd className="font-medium">
              {data.available_from === null ? "Not stated" : formatDate(data.available_from)}
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="vacancy-heading">
        <h2 id="vacancy-heading" className="text-lg font-semibold">
          Is anything free?
        </h2>
        <div className="mt-2">
          <VacancyNotice unit={data} />
          <p className="mt-1 text-sm text-muted-foreground">
            {vacancyExplanation(data.vacancy_freshness)}
          </p>
        </div>
      </section>

      <section aria-labelledby="details-heading">
        <h2 id="details-heading" className="text-lg font-semibold">
          The room
        </h2>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          <li>
            <Badge>{humanise(data.unit_type)}</Badge>
          </li>
          <li>
            <Badge>{humanise(data.furnished)}</Badge>
          </li>
          {data.bedrooms > 0 && (
            <li>
              <Badge>{count(data.bedrooms, "bedroom")}</Badge>
            </li>
          )}
          {data.size_sqm !== null && (
            <li>
              <Badge>{data.size_sqm} m²</Badge>
            </li>
          )}
          {data.floor !== null && (
            <li>
              <Badge>Floor {data.floor}</Badge>
            </li>
          )}
          {data.has_private_bathroom && (
            <li>
              <Badge>Private bathroom</Badge>
            </li>
          )}
          {data.has_kitchenette && (
            <li>
              <Badge>Kitchenette</Badge>
            </li>
          )}
        </ul>

        <h3 className="mt-4 text-sm font-semibold">Included in the rent</h3>
        {/* Stated in both directions. "Water included" listed and electricity
            silently absent reads as an oversight; token metering is the norm
            and a student budgeting for it needs to know which it is. */}
        <ul className="mt-1 space-y-1 text-sm">
          <Included label="Water" included={data.water_included} />
          <Included label="Electricity" included={data.electricity_included} />
          <Included label="Wifi" included={data.wifi_included} />
        </ul>
      </section>
    </Shell>
  );
}

function Included({ label, included }: { label: string; included: boolean }) {
  return (
    <li className="flex items-center gap-2">
      <span aria-hidden className="text-muted-foreground">
        {included ? "✓" : "✗"}
      </span>
      <span>
        {label}: <strong className="font-medium">{included ? "included" : "paid separately"}</strong>
      </span>
    </li>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 lg:py-10">{children}</div>;
}
