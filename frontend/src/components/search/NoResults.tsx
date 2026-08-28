import { SearchX } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LABELS, type Filters } from "@/features/listings/filters";
import type { Blame } from "@/features/listings/queries";

/**
 * The empty result, saying which filter is responsible.
 *
 * "Try adjusting your filters" is a shrug. It makes the student clear one box
 * at a time and wait for the network between each, which on a campus
 * connection is a minute of work to learn something the server already knows.
 *
 * The three cases are worded differently on purpose, because they are three
 * different facts:
 *
 * - one filter is to blame → name it, say what it is hiding, offer to drop it;
 * - several are jointly to blame → say that, and do not pick one at random;
 * - none is → **this is not the student's fault.** The campus has nothing
 *   listed that matches at all, and telling them to adjust their filters would
 *   be blaming them for the platform being empty.
 */

interface Props {
  filters: Filters;
  blames: Blame[];
  loading: boolean;
  hasFilters: boolean;
  onClear: (key: keyof Filters) => void;
  onClearAll: () => void;
}

export function NoResults({ filters, blames, loading, hasFilters, onClear, onClearAll }: Props) {
  return (
    <div className="mx-auto flex max-w-prose flex-col items-center gap-3 rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <SearchX aria-hidden className="size-7 text-muted-foreground" />
      <h2 className="text-lg font-semibold">No listings match this search</h2>

      {!hasFilters ? (
        <NothingListedYet />
      ) : loading ? (
        <p className="text-sm text-muted-foreground">Working out which filter is hiding them…</p>
      ) : blames.length === 1 ? (
        <SingleCause blame={blames[0]} onClear={onClear} />
      ) : blames.length > 1 ? (
        <SeveralCauses blames={blames} onClearAll={onClearAll} />
      ) : (
        <NoSingleCause filters={filters} onClearAll={onClearAll} />
      )}
    </div>
  );
}

function SingleCause({ blame, onClear }: { blame: Blame; onClear: Props["onClear"] }) {
  return (
    <>
      <p className="text-sm">
        Near this campus, <strong className="font-semibold">{blame.reason}</strong>.
      </p>
      {blame.key && (
        <>
          <p className="text-sm text-muted-foreground">
            Dropping the {LABELS[blame.key]} filter would show{" "}
            {blame.wouldShow === 1 ? "1 listing" : `${blame.wouldShow} listings`}.
          </p>
          <Button variant="outline" onClick={() => onClear(blame.key as keyof Filters)}>
            Drop the {LABELS[blame.key]} filter
          </Button>
        </>
      )}
    </>
  );
}

function SeveralCauses({ blames, onClearAll }: { blames: Blame[]; onClearAll: () => void }) {
  return (
    <>
      <p className="text-sm">
        No single filter is responsible — together they are too narrow. Any one of these
        would bring listings back:
      </p>
      <ul
        aria-label="Filters worth dropping"
        className="w-full space-y-1 text-left text-sm text-muted-foreground"
      >
        {blames.map((blame) => (
          <li key={String(blame.key)}>
            drop <strong className="font-medium text-foreground">{LABELS[blame.key!]}</strong> →{" "}
            {blame.wouldShow === 1 ? "1 listing" : `${blame.wouldShow} listings`}
          </li>
        ))}
      </ul>
      <Button variant="outline" onClick={onClearAll}>
        Clear all filters
      </Button>
    </>
  );
}

function NoSingleCause({ filters, onClearAll }: { filters: Filters; onClearAll: () => void }) {
  return (
    <>
      <p className="text-sm">
        {filters.q
          ? `Nothing near this campus matches “${filters.q}”, even with every other filter dropped.`
          : "Nothing near this campus matches, even with any one filter dropped."}
      </p>
      {/* Deliberately not "try adjusting your filters": at this point we have
          checked, and adjusting one of them will not help. */}
      <p className="text-sm text-muted-foreground">
        There may simply be little listed here yet. Saving a search you care about is
        the fastest way to hear when that changes.
      </p>
      <Button variant="outline" onClick={onClearAll}>
        Clear all filters
      </Button>
    </>
  );
}

function NothingListedYet() {
  return (
    <>
      {/* No filters are set, so there is nothing to blame the student for. */}
      <p className="text-sm">No landlord has published a listing near this campus yet.</p>
      <p className="text-sm text-muted-foreground">
        This is new here. If you know a landlord with rooms near campus, they can list
        them for free.
      </p>
    </>
  );
}
