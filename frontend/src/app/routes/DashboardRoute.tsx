import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useApplications,
  useInquiries,
  useTenancies,
  useWithdrawApplication,
} from "@/features/engagement/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { count, formatDate, formatKes, humanise } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * Where a student's own things live: their stays, their applications, their
 * questions.
 *
 * **Tenancy currency is asked for, never read off a field.** `?currency=current`
 * is a query parameter because currency is derived from the dates at query
 * time; there is no stored value meaning "active", and filtering on one would
 * return an empty page rather than an error — a wrong answer that looks like a
 * true one.
 *
 * **A null `end_date` means open-ended and still running.** Rendering it as
 * "unknown" or as a finished stay is the single most likely misread in the
 * whole contract, which is why the API states it three times.
 */
export default function DashboardRoute() {
  const user = useAuthStore((state) => state.user);
  const signedIn = useAuthStore((state) => state.status) === "authenticated";

  const current = useTenancies("current", signedIn);
  const applications = useApplications(signedIn);
  const inquiries = useInquiries(signedIn);

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-6 lg:py-10">
      <header>
        <h1 className="text-2xl font-semibold">Your place</h1>
        <p className="mt-1 text-sm text-muted-foreground">Signed in as {user?.email}.</p>
      </header>

      <section aria-labelledby="stays-heading">
        <h2 id="stays-heading" className="text-lg font-semibold">
          Where you live now
        </h2>
        <Panel
          query={current}
          empty="No current tenancy on record. One appears here when a landlord accepts an application, or when you claim a stay you arranged off-platform."
        >
          {(tenancies) => (
            <ul className="mt-3 space-y-3">
              {tenancies.map((tenancy) => (
                <li key={tenancy.id} className="rounded-lg border border-border p-4">
                  <h3 className="font-semibold">
                    <Link
                      to={`/listings/${tenancy.property_slug}`}
                      className="underline-offset-4 hover:underline"
                    >
                      {tenancy.property_name}
                    </Link>
                  </h3>
                  <p className="text-sm text-muted-foreground">{tenancy.unit_label}</p>
                  <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    <div>
                      <dt className="text-muted-foreground">Rent</dt>
                      <dd className="font-medium">{formatKes(tenancy.monthly_rent_kes)}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Since</dt>
                      <dd className="font-medium">{formatDate(tenancy.start_date)}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-muted-foreground">Until</dt>
                      <dd className="font-medium">
                        {tenancy.end_date === null
                          ? // Not "unknown" and not "—": an open-ended
                            // arrangement is a real one, and the words say so.
                            "No agreed end date — running until one of you ends it"
                          : formatDate(tenancy.end_date)}
                      </dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </section>

      <section aria-labelledby="applications-heading">
        <h2 id="applications-heading" className="text-lg font-semibold">
          Applications
        </h2>
        <Panel query={applications} empty="You have not applied for anywhere yet.">
          {(items) => <ApplicationList applications={items} />}
        </Panel>
      </section>

      <section aria-labelledby="inquiries-heading">
        <h2 id="inquiries-heading" className="text-lg font-semibold">
          Questions you asked
        </h2>
        <Panel query={inquiries} empty="You have not asked a landlord anything yet.">
          {(items) => (
            <ul className="mt-3 space-y-3">
              {items.map((inquiry) => (
                <li key={inquiry.id} className="rounded-lg border border-border p-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="font-semibold">
                      <Link
                        to={`/listings/${inquiry.property_slug}`}
                        className="underline-offset-4 hover:underline"
                      >
                        {inquiry.property_name}
                      </Link>
                      <span className="ml-1 font-normal text-muted-foreground">
                        · {inquiry.unit_label}
                      </span>
                    </h3>
                    <Badge>{humanise(inquiry.status)}</Badge>
                  </div>

                  <p className="mt-2 whitespace-pre-line text-sm">{inquiry.message}</p>

                  {inquiry.response ? (
                    <aside className="mt-3 rounded-md border-l-2 border-border bg-muted/40 p-3">
                      <p className="text-xs font-medium">
                        {/* Who answered, because the student is owed the
                            knowledge that a person did -- and which one. */}
                        {inquiry.responded_by_name ?? "The landlord"} replied ·{" "}
                        {formatDate(inquiry.responded_at)}
                      </p>
                      <p className="mt-1 whitespace-pre-line text-sm">{inquiry.response}</p>
                    </aside>
                  ) : (
                    <p className="mt-2 text-xs text-muted-foreground">
                      No reply yet. Landlords are not obliged to answer.
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </section>
    </div>
  );
}

function ApplicationList({ applications }: { applications: Schemas["Application"][] }) {
  const withdraw = useWithdrawApplication();

  return (
    <ul className="mt-3 space-y-3">
      {applications.map((application) => (
        <li key={application.id} className="rounded-lg border border-border p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-semibold">
              <Link
                to={`/listings/${application.property_slug}`}
                className="underline-offset-4 hover:underline"
              >
                {application.property_name}
              </Link>
              <span className="ml-1 font-normal text-muted-foreground">
                · {application.unit_label}
              </span>
            </h3>
            <Badge variant={application.status === "accepted" ? "note" : "neutral"}>
              {humanise(application.status)}
            </Badge>
          </div>

          <p className="mt-1 text-sm text-muted-foreground">
            Move in {formatDate(application.move_in_date)} ·{" "}
            {count(application.intended_months, "month")}
          </p>

          {application.decision_note && (
            <p className="mt-2 text-sm">
              <span className="text-muted-foreground">The landlord said: </span>
              {application.decision_note}
            </p>
          )}

          {(application.status === "submitted" || application.status === "under_review") && (
            <Button
              variant="ghost"
              size="sm"
              className="mt-2"
              disabled={withdraw.isPending}
              onClick={() => withdraw.mutate(application.id)}
            >
              Withdraw
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The three panels share one loading, error and empty treatment, so a slow
 *  or failing section never takes the others down with it. */
function Panel<T>({
  query,
  empty,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data?: { count: number; results: T[] };
  };
  empty: string;
  children: (items: T[]) => React.ReactNode;
}) {
  if (query.isPending) {
    return (
      <p role="status" className="mt-2 text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (query.isError) {
    return (
      <p role="alert" className="mt-2 text-sm text-muted-foreground">
        {userFacingMessage(toApiError(query.error))}
      </p>
    );
  }

  if (!query.data || query.data.count === 0) {
    return <p className="mt-2 max-w-prose text-sm text-muted-foreground">{empty}</p>;
  }

  return <>{children(query.data.results)}</>;
}
