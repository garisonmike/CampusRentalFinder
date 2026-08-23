/**
 * Keyboard users tab into this first; it jumps past the header to the main
 * landmark. Visually hidden until focused.
 */
export function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only rounded-md bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
    >
      Skip to main content
    </a>
  );
}
