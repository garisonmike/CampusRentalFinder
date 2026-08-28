import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useSavedProperties, useUnsaveProperty } from "@/features/engagement/queries";
import { formatDate } from "@/lib/format";
import { toApiError, userFacingMessage } from "@/lib/api-error";

/**
 * The student's saved listings.
 *
 * The note on a save is **private** — the API says so and so does this page,
 * because a student writing "landlord seemed evasive" needs to know before
 * they write it, not after.
 */
export default function SavedRoute() {
  const saved = useSavedProperties(true);
  const unsave = useUnsaveProperty();

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 lg:py-10">
      <h1 className="text-2xl font-semibold">Saved listings</h1>

      {saved.isPending ? (
        <p role="status" className="mt-4 text-sm text-muted-foreground">
          Loading your saved listings…
        </p>
      ) : saved.isError ? (
        <p role="alert" className="mt-4 text-sm text-muted-foreground">
          {userFacingMessage(toApiError(saved.error))}
        </p>
      ) : saved.data.count === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-border p-8 text-center">
          <p className="font-medium">Nothing saved yet</p>
          <p className="mx-auto mt-1 max-w-prose text-sm text-muted-foreground">
            Saving a listing keeps it here with a private note, so you can compare places
            after a few viewings instead of trying to remember which one had the water
            tank.
          </p>
          <Button asChild variant="outline" className="mt-4">
            <Link to="/listings">Browse listings</Link>
          </Button>
        </div>
      ) : (
        <ul className="mt-6 space-y-3">
          {saved.data.results.map((entry) => (
            <li
              key={entry.id}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border p-4"
            >
              <div>
                <h2 className="font-semibold">
                  <Link
                    to={`/listings/${entry.property_slug}`}
                    className="underline-offset-4 hover:underline"
                  >
                    {entry.property_name}
                  </Link>
                </h2>
                <p className="text-sm text-muted-foreground">{entry.property_town}</p>
                {entry.note && (
                  <p className="mt-2 text-sm">
                    <span className="text-muted-foreground">Your private note: </span>
                    {entry.note}
                  </p>
                )}
                <p className="mt-1 text-xs text-muted-foreground">
                  Saved {formatDate(entry.created_at)}
                </p>
              </div>

              <Button
                variant="ghost"
                size="sm"
                disabled={unsave.isPending}
                onClick={() => unsave.mutate(entry.property_slug)}
                aria-label={`Remove ${entry.property_name} from your saved listings`}
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
