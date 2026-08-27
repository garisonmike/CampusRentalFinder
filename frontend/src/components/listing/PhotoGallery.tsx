import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ImageOff, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Schemas } from "@/api/types";

/**
 * The photo strip. No carousel library.
 *
 * A carousel package is 15–40 kB gzipped, against an entry budget of 130 kB
 * for a student on a mid-range Android on mobile data. What it buys is drag
 * physics, and what the browser already gives us for free is
 * `scroll-snap-type` — which has real momentum, real touch handling, and works
 * before any JavaScript has run. The buttons and the keyboard handling below
 * are about eighty lines. That trade did not need arguing.
 *
 * The scroll container is the source of truth, not a state variable driving a
 * transform: a swipe must move the photos whether or not React heard about it,
 * and an index that disagrees with what the user is looking at is worse than
 * no index. State follows scroll, never the reverse — except when a button or
 * a key press asks, and then we scroll and let the handler catch up.
 *
 * Three states, and the empty one came first:
 * - **no photos**: said plainly. A listing with no photos is one you cannot
 *   judge, and a stock image would be the platform inventing evidence.
 * - **still processing**: the resize job is queued (ADR-007). The slot is
 *   held rather than the photo dropped, so the count stays honest.
 * - **ready**: the photo, with its caption if it has one.
 */

type Photo = Schemas["UnitPhoto"];

interface Props {
  photos: readonly Photo[];
  /** Names the gallery for a screen reader: "Photos of Bedsitters". */
  label: string;
  className?: string;
}

export function PhotoGallery({ photos, label, className }: Props) {
  const trackRef = useRef<HTMLUListElement>(null);
  const [index, setIndex] = useState(0);

  const scrollTo = useCallback((next: number) => {
    const track = trackRef.current;
    if (!track) return;

    setIndex(next);
    // Guarded: jsdom has no layout and no scrollTo. A gallery that throws in
    // a test suite is a gallery nobody tests.
    track.scrollTo?.({ left: next * track.clientWidth, behavior: "smooth" });
  }, []);

  const onScroll = useCallback(() => {
    const track = trackRef.current;
    if (!track || !track.clientWidth) return;

    setIndex(Math.round(track.scrollLeft / track.clientWidth));
  }, []);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        scrollTo(Math.min(index + 1, photos.length - 1));
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        scrollTo(Math.max(index - 1, 0));
      }
    },
    [index, photos.length, scrollTo],
  );

  // A photo removed under us must not leave the index pointing past the end.
  useEffect(() => {
    if (index > photos.length - 1) setIndex(Math.max(photos.length - 1, 0));
  }, [index, photos.length]);

  if (photos.length === 0) {
    return <NoPhotos className={className} />;
  }

  return (
    <section
      aria-roledescription="carousel"
      aria-label={label}
      className={cn("relative", className)}
      onKeyDown={onKeyDown}
    >
      <ul
        ref={trackRef}
        onScroll={onScroll}
        tabIndex={0}
        className={
          "flex snap-x snap-mandatory overflow-x-auto rounded-lg " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
          "[scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        }
      >
        {photos.map((photo, position) => (
          <li
            key={photo.id}
            aria-label={`${position + 1} of ${photos.length}`}
            aria-roledescription="slide"
            className="w-full shrink-0 snap-center"
          >
            <PhotoSlot photo={photo} />
          </li>
        ))}
      </ul>

      {photos.length > 1 && (
        <>
          <Arrow
            direction="previous"
            disabled={index === 0}
            onClick={() => scrollTo(index - 1)}
          />
          <Arrow
            direction="next"
            disabled={index === photos.length - 1}
            onClick={() => scrollTo(index + 1)}
          />

          {/* Dots are decoration; the live region is the actual announcement.
              A position conveyed only by which dot is filled is a position
              conveyed only by colour. */}
          <p aria-live="polite" className="sr-only">
            Photo {index + 1} of {photos.length}
          </p>
          <ol aria-hidden className="mt-2 flex justify-center gap-1.5">
            {photos.map((photo, position) => (
              <li
                key={photo.id}
                className={cn(
                  "h-1.5 rounded-full transition-all",
                  position === index ? "w-4 bg-foreground" : "w-1.5 bg-foreground/25",
                )}
              />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function Arrow({
  direction,
  disabled,
  onClick,
}: {
  direction: "previous" | "next";
  disabled: boolean;
  onClick: () => void;
}) {
  const Icon = direction === "previous" ? ChevronLeft : ChevronRight;

  return (
    <Button
      type="button"
      variant="secondary"
      size="icon"
      disabled={disabled}
      onClick={onClick}
      // Named, not implied by which side it sits on. "Button" is what a screen
      // reader says for an icon with no label.
      aria-label={`${direction === "previous" ? "Previous" : "Next"} photo`}
      className={cn(
        "absolute top-1/2 size-9 -translate-y-1/2 rounded-full opacity-90 shadow-md",
        direction === "previous" ? "left-2" : "right-2",
      )}
    >
      <Icon aria-hidden />
    </Button>
  );
}

function PhotoSlot({ photo }: { photo: Photo }) {
  if (photo.url === null) {
    return (
      <div className="flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 bg-muted text-muted-foreground">
        <Loader2 aria-hidden className="size-5 animate-spin" />
        <p className="text-sm">Photo still uploading</p>
      </div>
    );
  }

  return (
    <figure className="m-0">
      <img
        src={photo.url}
        // A caption is the landlord's own description; without one the photo
        // is decorative *relative to the listing text beside it*, and an
        // invented alt ("a room") would be the platform describing a picture
        // it has not looked at.
        alt={photo.caption || ""}
        loading="lazy"
        decoding="async"
        className="aspect-[4/3] w-full bg-muted object-cover"
      />
      {photo.caption && (
        <figcaption className="px-1 pt-1 text-xs text-muted-foreground">
          {photo.caption}
        </figcaption>
      )}
    </figure>
  );
}

function NoPhotos({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex aspect-[4/3] w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/50 px-4 text-center",
        className,
      )}
    >
      <ImageOff aria-hidden className="size-6 text-muted-foreground" />
      <p className="text-sm font-medium">No photos yet</p>
      <p className="max-w-[28ch] text-xs text-muted-foreground">
        Ask the landlord for photos before you travel to view it.
      </p>
    </div>
  );
}
