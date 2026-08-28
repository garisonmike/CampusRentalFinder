import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useCloseInquiry,
  useRespondToInquiry,
  useConfirmClaim,
  useDecideApplication,
  useDisputeClaim,
  useIncomingApplications,
  useIncomingClaims,
  useIncomingInquiries,
} from "@/features/portal/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { count, formatDate, formatKes, humanise } from "@/lib/format";
import { useAuthStore } from "@/stores/auth";
import type { Schemas } from "@/api/types";

/**
 * The landlord and caretaker portal.
 *
 * Ordered by whose time is being spent. A claim has a **deadline and silence
 * confirms it** (ADR-004) — a landlord who ignores this page for two weeks has
 * agreed to every claim on it — so claims come first and say when they expire.
 * Applications are somebody's housing decision waiting on a reply. Inquiries
 * are questions, and questions can wait.
 *
 * **A caretaker sees everything here except the review reply**, which is not
 * on this page for either role: speaking for the business in public is the
 * owner's own act (ADR-003), and a portal that mixed the two would make a
 * caretaker's confirmation and a caretaker's opinion look like the same
 * authority.
 */
export default function PortalRoute() {
  const signedIn = useAuthStore((state) => state.status) === "authenticated";
  const isLandlord = useAuthStore((state) => state.hasRole("landlord"));

  const claims = useIncomingClaims(signedIn);
  const applications = useIncomingApplications(signedIn);
  const inquiries = useIncomingInquiries(signedIn);

  const pendingClaims = (claims.data?.results ?? []).filter(
    (claim) => claim.status === "pending",
  );
  const openApplications = (applications.data?.results ?? []).filter(
    (application) =>
      application.status === "submitted" || application.status === "under_review",
  );

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-6 lg:py-10">
      <header>
        <h1 className="text-2xl font-semibold">Your properties</h1>
        {!isLandlord && (
          // A caretaker is not the owner, and the page says so rather than
          // letting them discover it from a 403 on the one action they cannot
          // take.
          <p className="mt-1 text-sm text-muted-foreground">
            You are a caretaker here. You can confirm stays and answer questions; replying
            to a review publicly is the owner's own act.
          </p>
        )}
      </header>

      <section aria-labelledby="claims-heading">
        <h2 id="claims-heading" className="text-lg font-semibold">
          Stays waiting on you
        </h2>
        {/* The consequence is stated before the list, not after it. Silence
            here is a decision, and a page that let somebody discover that
            afterwards would have made it for them. */}
        <p className="mt-1 max-w-prose text-sm text-muted-foreground">
          A student says they lived at one of your properties. If you do nothing before
          the deadline, the claim is confirmed — silence is a signal, not a veto.
        </p>

        <Panel query={claims} empty="Nobody is waiting on you.">
          {() =>
            pendingClaims.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">Nobody is waiting on you.</p>
            ) : (
              <ul className="mt-3 space-y-3">
                {pendingClaims.map((claim) => (
                  <li key={claim.id}>
                    <ClaimRow claim={claim} />
                  </li>
                ))}
              </ul>
            )
          }
        </Panel>
      </section>

      <section aria-labelledby="applications-heading">
        <h2 id="applications-heading" className="text-lg font-semibold">
          Applications to decide
        </h2>
        <Panel query={applications} empty="No applications yet.">
          {() =>
            openApplications.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Nothing to decide right now.
              </p>
            ) : (
              <ul className="mt-3 space-y-3">
                {openApplications.map((application) => (
                  <li key={application.id}>
                    <ApplicationRow application={application} />
                  </li>
                ))}
              </ul>
            )
          }
        </Panel>
      </section>

      <section aria-labelledby="questions-heading">
        <h2 id="questions-heading" className="text-lg font-semibold">
          Questions from students
        </h2>
        <Panel query={inquiries} empty="No questions yet.">
          {(items) => (
            <ul className="mt-3 space-y-3">
              {items.map((inquiry) => (
                <li key={inquiry.id}>
                  <InquiryRow inquiry={inquiry} />
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </section>
    </div>
  );
}

function ClaimRow({ claim }: { claim: Schemas["TenancyClaim"] }) {
  const confirm = useConfirmClaim();
  const dispute = useDisputeClaim();
  const [reason, setReason] = useState<Schemas["DisputeRequest"]["reason"] | "">("");

  return (
    <article className="rounded-lg border border-border p-4">
      <h3 className="font-semibold">
        {claim.claimant_name}
        <span className="ml-1 font-normal text-muted-foreground">
          · {claim.property_name}, {claim.unit_label}
        </span>
      </h3>

      <p className="mt-1 text-sm text-muted-foreground">
        {formatDate(claim.start_date)} —{" "}
        {claim.end_date === null ? "still living there" : formatDate(claim.end_date)} ·{" "}
        {formatKes(claim.monthly_rent_kes)} a month
      </p>

      {claim.confirmation_deadline && (
        <p className="mt-1 text-sm font-medium">
          Confirmed automatically after {formatDate(claim.confirmation_deadline)} unless you
          say otherwise.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Button
          size="sm"
          disabled={confirm.isPending}
          onClick={() => confirm.mutate(claim.id)}
        >
          Yes, they lived here
        </Button>

        <div>
          <label htmlFor={`dispute-${claim.id}`} className="mb-1 block text-xs font-medium">
            Or dispute it
          </label>
          <div className="flex gap-2">
            <select
              id={`dispute-${claim.id}`}
              value={reason}
              onChange={(event) =>
                setReason(event.target.value as Schemas["DisputeRequest"]["reason"])
              }
              className="rounded-md border border-input bg-background px-2 py-1.5 text-sm"
            >
              <option value="">Choose a reason</option>
              {/* Enumerated, because an untyped dispute cannot be routed and
                  can therefore only go to a human. */}
              <option value="dates_incorrect">The stay happened; the dates are wrong</option>
              <option value="never_tenanted">This person never lived here</option>
              <option value="duplicate">Already covered by an existing tenancy</option>
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={reason === "" || dispute.isPending}
              onClick={() => reason !== "" && dispute.mutate({ id: claim.id, reason })}
            >
              Dispute
            </Button>
          </div>
        </div>
      </div>

      <MutationError error={confirm.error ?? dispute.error} />
    </article>
  );
}

function ApplicationRow({ application }: { application: Schemas["Application"] }) {
  const accept = useDecideApplication("accept");
  const reject = useDecideApplication("reject");
  const [note, setNote] = useState("");

  return (
    <article className="rounded-lg border border-border p-4">
      <h3 className="font-semibold">
        {application.applicant_name}
        <span className="ml-1 font-normal text-muted-foreground">
          · {application.property_name}, {application.unit_label}
        </span>
      </h3>

      <p className="mt-1 text-sm text-muted-foreground">
        Wants to move in {formatDate(application.move_in_date)} for{" "}
        {count(application.intended_months, "month")}
      </p>

      {application.message && (
        <p className="mt-2 whitespace-pre-line text-sm">{application.message}</p>
      )}

      <div className="mt-3 space-y-2">
        <label htmlFor={`note-${application.id}`} className="block text-xs font-medium">
          A note for them{" "}
          <span className="font-normal text-muted-foreground">
            — a rejection with no reason gives them nothing to act on
          </span>
        </label>
        <textarea
          id={`note-${application.id}`}
          rows={2}
          maxLength={500}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={accept.isPending}
            onClick={() => accept.mutate({ id: application.id, note })}
          >
            Accept
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={reject.isPending}
            onClick={() => reject.mutate({ id: application.id, note })}
          >
            Reject
          </Button>
        </div>
        {/* Said before they press it, not after. Accepting creates a
            confirmed tenancy outright -- there is no confirmation window and
            no dispute surface behind it (ADR-004 §1.1). */}
        <p className="text-xs text-muted-foreground">
          Accepting creates the tenancy immediately. There is no confirmation step after
          this.
        </p>
      </div>

      <MutationError error={accept.error ?? reject.error} />
    </article>
  );
}

function InquiryRow({ inquiry }: { inquiry: Schemas["Inquiry"] }) {
  const respond = useRespondToInquiry();
  const close = useCloseInquiry();
  const [reply, setReply] = useState("");

  return (
    <article className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">
          {inquiry.sender_name}
          <span className="ml-1 font-normal text-muted-foreground">
            · {inquiry.property_name}, {inquiry.unit_label}
          </span>
        </h3>
        <Badge>{humanise(inquiry.status)}</Badge>
      </div>

      <p className="mt-2 whitespace-pre-line text-sm">{inquiry.message}</p>
      {inquiry.preferred_move_in_date && (
        <p className="mt-1 text-xs text-muted-foreground">
          Hoping to move in {formatDate(inquiry.preferred_move_in_date)}
        </p>
      )}

      {inquiry.response ? (
        <aside className="mt-3 rounded-md border-l-2 border-border bg-muted/40 p-3 text-sm">
          <p className="text-xs font-medium">
            {inquiry.responded_by_name ?? "You"} replied · {formatDate(inquiry.responded_at)}
          </p>
          <p className="mt-1 whitespace-pre-line">{inquiry.response}</p>
        </aside>
      ) : (
        <div className="mt-3 space-y-2">
          <label htmlFor={`reply-${inquiry.id}`} className="block text-sm font-medium">
            Reply
          </label>
          {/* Same rule in this direction, and said before they write.
              "Call me on 07..." from a landlord is the same leak with the
              same consequence, and it is the reply people reach for first. */}
          <p id={`reply-rules-${inquiry.id}`} className="text-xs text-muted-foreground">
            Phone numbers, email addresses and messaging handles are rejected here too.
            Invite them to apply instead — that is the path the platform can witness, and
            it is what lets you both prove the stay later.
          </p>
          <textarea
            id={`reply-${inquiry.id}`}
            aria-describedby={`reply-rules-${inquiry.id}`}
            rows={3}
            maxLength={2000}
            value={reply}
            onChange={(event) => setReply(event.target.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={reply.trim() === "" || respond.isPending}
              onClick={() => respond.mutate({ id: inquiry.id, response: reply })}
            >
              Send reply
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={close.isPending}
              onClick={() => close.mutate(inquiry.id)}
            >
              Close without replying
            </Button>
          </div>
        </div>
      )}

      <MutationError error={respond.error ?? close.error} />
    </article>
  );
}

function MutationError({ error }: { error: unknown }) {
  if (!error) return null;

  return (
    <p role="alert" className="mt-2 text-sm font-medium">
      {userFacingMessage(toApiError(error))}
    </p>
  );
}

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
