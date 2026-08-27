/**
 * Brand palettes nobody would design against on purpose.
 *
 * A tenant sets its own colour (ADR-005) and the design has to hold in all of
 * them. These four are the corners: too dark to show a button, too light to
 * take white text, saturated enough that every action looks destructive, and
 * so achromatic that "the primary colour draws the eye" stops being true.
 *
 * Kept in a plain module rather than in the test that sweeps the shell,
 * because every component suite renders under them and importing from a
 * `.test` file would run that suite again inside each one.
 */

export const HOSTILE_PALETTES: Array<{
  name: string;
  primary: string;
  secondary: string;
  accent: string;
  hazard: string;
}> = [
  {
    name: "very dark navy",
    primary: "222 60% 11%",
    secondary: "222 30% 24%",
    accent: "222 40% 92%",
    hazard: "A primary button becomes a hole in the page; borders vanish.",
  },
  {
    name: "very light yellow",
    primary: "52 98% 62%",
    secondary: "45 90% 78%",
    accent: "52 100% 94%",
    hazard: "White text on it is invisible; it reads as a warning colour.",
  },
  {
    name: "saturated red",
    primary: "0 88% 52%",
    secondary: "12 70% 44%",
    accent: "0 90% 95%",
    hazard: "Every primary action looks destructive.",
  },
  {
    name: "low-chroma grey",
    primary: "210 4% 46%",
    secondary: "210 3% 62%",
    accent: "210 5% 92%",
    hazard: "Primary and muted collapse into each other; nothing draws the eye.",
  },
];
