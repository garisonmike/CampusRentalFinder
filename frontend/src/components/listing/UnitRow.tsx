import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { VacancyNotice, vacancyExplanation } from "@/components/listing/VacancyNotice";
import { count, formatDate, formatKes, humanise } from "@/lib/format";
import type { Schemas } from "@/api/types";

/**
 * One unit inside a property.
 *
 * A `Unit` row can be a single room ("B12") or a **pool of identical rooms**
 * ("Bedsitters", 40 of them, 6 free). Everything here is worded to survive
 * both: "6 of 40 free" rather than "available", because "available" for a pool
 * is a question about a count and not a yes or no.
 *
 * The deposit is shown beside the rent rather than buried. A student comparing
 * two listings at 8,500 is really comparing 8,500 and 17,000 on move-in day,
 * and the one that mentions the deposit only after they have travelled to view
 * it is the one that wastes their fare.
 */
export function UnitRow({
  unit,
  propertySlug,
}: {
  unit: Schemas["UnitSummary"];
  propertySlug: string;
}) {
  return (
    <article className="relative rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-base font-semibold">
          <Link
            to={`/listings/${propertySlug}/units/${unit.id}`}
            className="after:absolute after:inset-0 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {unit.label}
          </Link>
        </h3>
        <p className="text-lg font-bold">
          {formatKes(unit.rent_kes)}
          <span className="text-sm font-normal text-muted-foreground"> a month</span>
        </p>
      </div>

      <ul className="mt-2 flex flex-wrap gap-1.5">
        <li>
          <Badge>{humanise(unit.unit_type)}</Badge>
        </li>
        <li>
          <Badge>{humanise(unit.furnished)}</Badge>
        </li>
        {unit.bedrooms > 0 && (
          <li>
            <Badge>{count(unit.bedrooms, "bedroom")}</Badge>
          </li>
        )}
        {unit.size_sqm !== null && (
          <li>
            <Badge>{unit.size_sqm} m²</Badge>
          </li>
        )}
      </ul>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-muted-foreground">Deposit</dt>
          <dd className="font-medium">
            {unit.deposit_kes === null ? "Not stated" : formatKes(unit.deposit_kes)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Minimum stay</dt>
          <dd className="font-medium">{count(unit.min_stay_months, "month")}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Available from</dt>
          <dd className="font-medium">
            {unit.available_from === null ? "Not stated" : formatDate(unit.available_from)}
          </dd>
        </div>
      </dl>

      <div className="mt-3 border-t border-border pt-3">
        <VacancyNotice unit={unit} />
        <p className="mt-1 text-xs text-muted-foreground">
          {vacancyExplanation(unit.vacancy_freshness)}
        </p>
      </div>
    </article>
  );
}
