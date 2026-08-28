import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useSendInquiry } from "@/features/engagement/queries";
import { fieldError, nonFieldErrors, toApiError, userFacingMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/auth";

/**
 * Ask the landlord about a room.
 *
 * **The contact-details rule is stated before the student writes, not after
 * the API rejects them.** A message with a phone number in it is refused, and
 * discovering that from a red box under a paragraph you have just typed reads
 * as an obstruction. Said up front, with the reason, it reads as what it is:
 * keeping the conversation here is what lets the platform confirm a resulting
 * stay later without either party having to prove it (ADR-004 §1.1).
 *
 * The success state does not pretend to more than happened. "Sent" — not
 * "the landlord will reply shortly", which is a promise made on somebody
 * else's behalf, by a platform with no way to keep it.
 */
export function InquiryForm({ unitId, unitLabel }: { unitId: number; unitLabel: string }) {
  const status = useAuthStore((state) => state.status);
  const [message, setMessage] = useState("");
  const [moveIn, setMoveIn] = useState("");
  const send = useSendInquiry();

  if (status !== "authenticated") {
    return (
      <p className="text-sm text-muted-foreground">
        <Link to="/login" className="font-medium underline underline-offset-4">
          Sign in
        </Link>{" "}
        to ask the landlord about this room.
      </p>
    );
  }

  if (send.isSuccess) {
    return (
      <div role="status" className="rounded-lg border border-border p-4">
        <p className="text-sm font-medium">Your question was sent.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          You will see the reply here and in your dashboard. Landlords are not obliged to
          answer, and some do not.
        </p>
      </div>
    );
  }

  const error = send.error ? toApiError(send.error) : null;

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        send.mutate({
          unit: unitId,
          message,
          preferred_move_in_date: moveIn === "" ? null : moveIn,
        });
      }}
    >
      <div>
        <label htmlFor="inquiry-message" className="mb-1 block text-sm font-medium">
          Ask about {unitLabel}
        </label>
        <p id="inquiry-rules" className="mb-1 text-xs text-muted-foreground">
          Please do not include a phone number, email address or messaging handle — they
          are rejected. Keeping the conversation here is what lets us confirm your stay
          later without you having to prove it.
        </p>
        <textarea
          id="inquiry-message"
          aria-describedby="inquiry-rules"
          required
          maxLength={2000}
          rows={4}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {error && fieldError(error, "message") && (
          <p role="alert" className="mt-1 text-sm font-medium">
            {fieldError(error, "message")}
          </p>
        )}
      </div>

      <div>
        <label htmlFor="inquiry-move-in" className="mb-1 block text-sm font-medium">
          When would you move in? <span className="text-muted-foreground">(optional)</span>
        </label>
        <input
          id="inquiry-move-in"
          type="date"
          value={moveIn}
          onChange={(event) => setMoveIn(event.target.value)}
          className="rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {error && !fieldError(error, "message") && (
        <p role="alert" className="text-sm font-medium">
          {nonFieldErrors(error)[0] ?? userFacingMessage(error)}
        </p>
      )}

      <Button type="submit" disabled={send.isPending}>
        {send.isPending ? "Sending…" : "Send question"}
      </Button>
    </form>
  );
}
