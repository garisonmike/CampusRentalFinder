import { Link } from "react-router-dom";
import { Bookmark, BookmarkCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  isSaved,
  useSaveProperty,
  useSavedProperties,
  useUnsaveProperty,
} from "@/features/engagement/queries";
import { toApiError, userFacingMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/auth";

/**
 * Save a listing, or say why you cannot.
 *
 * A signed-out visitor gets a link to sign in rather than a button that
 * appears to work and then fails — the failure would arrive after the tap,
 * with the listing lost behind a redirect.
 *
 * Saving is idempotent server-side, so a double tap is not an error and this
 * does not try to prevent one locally. What it does prevent is the *pending*
 * state being invisible: on a slow connection a student taps twice because
 * nothing happened, and a button that says nothing about being busy is what
 * taught them to.
 */
export function SaveButton({ slug, name }: { slug: string; name: string }) {
  const status = useAuthStore((state) => state.status);
  const signedIn = status === "authenticated";

  const saved = useSavedProperties(signedIn);
  const save = useSaveProperty();
  const unsave = useUnsaveProperty();

  if (!signedIn) {
    return (
      <Button asChild variant="outline">
        <Link to="/login" state={{ from: `/listings/${slug}` }}>
          <Bookmark aria-hidden />
          Sign in to save
        </Link>
      </Button>
    );
  }

  const entry = isSaved(saved.data, slug);
  const busy = save.isPending || unsave.isPending;
  const error = save.error ?? unsave.error;

  return (
    <div className="space-y-1">
      <Button
        variant={entry ? "secondary" : "outline"}
        disabled={busy || saved.isPending}
        onClick={() => (entry ? unsave.mutate(slug) : save.mutate({ property_slug: slug }))}
        // The accessible name says which way the press goes. "Save" on a
        // button that unsaves is the kind of thing only sighted users catch,
        // from an icon.
        aria-label={entry ? `Remove ${name} from your saved listings` : `Save ${name}`}
      >
        {entry ? <BookmarkCheck aria-hidden /> : <Bookmark aria-hidden />}
        {busy ? "Saving…" : entry ? "Saved" : "Save"}
      </Button>

      {error && (
        <p role="alert" className="text-xs text-muted-foreground">
          {userFacingMessage(toApiError(error))}
        </p>
      )}
    </div>
  );
}
