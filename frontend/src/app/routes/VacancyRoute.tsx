import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useManagedProperties, useStateVacancy } from "@/features/portal/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { formatAgeInDays } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * Where the vacancy prompt email lands.
 *
 * The email says "update these counts" and this is the screen that does it,
 * one click away. That link is the entire reason the job was held: a prompt
 * that drops somebody on a home page teaches them not to open the next one,
 * and this is the only channel the freshness mechanism has.
 *
 * So the page is ordered by what the email complained about. Stale and
 * never-stated units come first, with their age; everything else is below,
 * because a landlord who arrives from that email is here to fix three rooms,
 * not to browse their portfolio.
 *
 * **Confirming an unchanged count is a real action, not a no-op.** "Still 6
 * free" is new information -- it is a fresh statement of the same number --
 * and the button says so. A page that only offered "change" would leave the
 * landlord with nothing to press when nothing had changed, which is the most
 * common case.
 */
export default function VacancyRoute() {
  const signedIn = useAuthStore((state) => state.status) === "authenticated";
  const properties = useManagedProperties(signedIn);

  if (properties.isPending) {
    return (
      <Shell>
        <p role="status" className="text-sm text-muted-foreground">
          Loading your rooms…
        </p>
      </Shell>
    );
  }

  if (properties.isError) {
    return (
      <Shell>
        <p role="alert" className="text-sm text-muted-foreground">
          {userFacingMessage(toApiError(properties.error))}
        </p>
      </Shell>
    );
  }

  const rows = properties.data.flatMap((property) =>
    property.units.map((unit) => ({ property, unit })),
  );
  const needsAttention = rows.filter(
    ({ unit }) =>
      unit.vacancy_freshness === "stale" || unit.vacancy_freshness === "unknown",
  );
  const rest = rows.filter(
    ({ unit }) => unit.vacancy_freshness === "fresh" || unit.vacancy_freshness === "ageing",
  );

  return (
    <Shell>
      <header>
        <h1 className="text-2xl font-semibold">Are these still available?</h1>
        <p className="mt-1 max-w-prose text-sm text-muted-foreground">
          Students see these counts when they search, and we show them how old each one
          is. Confirming a number that has not changed is worth as much as changing one —
          it makes the count current.
        </p>
      </header>

      {rows.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          You have no rooms listed yet. Add a property and its units first, and this page
          will list them.
        </p>
      ) : (
        <>
          <section aria-labelledby="stale-heading">
            <h2 id="stale-heading" className="text-lg font-semibold">
              Worth updating first
            </h2>
            {needsAttention.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Nothing is out of date. Every count has been confirmed recently.
              </p>
            ) : (
              <ul className="mt-3 space-y-3">
                {needsAttention.map(({ property, unit }) => (
                  <li key={unit.id}>
                    <VacancyRow property={property} unit={unit} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {rest.length > 0 && (
            <section aria-labelledby="fresh-heading">
              <h2 id="fresh-heading" className="text-lg font-semibold">
                Recently confirmed
              </h2>
              <ul className="mt-3 space-y-3">
                {rest.map(({ property, unit }) => (
                  <li key={unit.id}>
                    <VacancyRow property={property} unit={unit} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Shell>
  );
}

function VacancyRow({
  property,
  unit,
}: {
  property: Schemas["PropertyDetail"];
  unit: Schemas["UnitSummary"];
}) {
  const [count, setCount] = useState(unit.vacant_count);
  const state = useStateVacancy(property.slug);
  const inputId = `vacancy-${unit.id}`;

  const unchanged = count === unit.vacant_count;

  return (
    <article className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">
          <Link
            to={`/listings/${property.slug}`}
            className="underline-offset-4 hover:underline"
          >
            {property.name}
          </Link>
          <span className="ml-1 font-normal text-muted-foreground">· {unit.label}</span>
        </h3>
        <Badge variant={unit.vacancy_freshness === "fresh" ? "neutral" : "note"}>
          {unit.vacancy_freshness === "unknown"
            ? "Never confirmed"
            : `Last confirmed ${formatAgeInDays(unit.vacancy_age_days)}`}
        </Badge>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor={inputId} className="mb-1 block text-sm font-medium">
            Rooms free of {unit.total_count}
          </label>
          <input
            id={inputId}
            type="number"
            inputMode="numeric"
            min={0}
            max={unit.total_count}
            value={count}
            onChange={(event) => setCount(Number(event.target.value))}
            className="w-24 rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </div>

        <Button
          disabled={state.isPending}
          onClick={() => state.mutate({ unitId: unit.id, vacantCount: count })}
        >
          {/* Two labels, one action. Confirming an unchanged count IS the
              action for most rows, and a button offering only "update" leaves
              the commonest case with nothing to press. */}
          {state.isPending ? "Saving…" : unchanged ? `Still ${count} free` : "Update"}
        </Button>

        {state.isSuccess && (
          <p role="status" className="text-sm text-muted-foreground">
            Confirmed just now. Students will see it as current.
          </p>
        )}
      </div>

      {state.isError && (
        <p role="alert" className="mt-2 text-sm font-medium">
          {userFacingMessage(toApiError(state.error))}
        </p>
      )}
    </article>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-3xl space-y-8 px-4 py-6 lg:py-10">{children}</div>;
}
