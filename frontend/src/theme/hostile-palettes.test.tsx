import { axe } from "vitest-axe";
import { describe, expect, it } from "vitest";

import { buildTokens, contrastRatio, parseHsl, type Hsl } from "./tokens";
import { renderWithProviders } from "@/test/utils";

/**
 * The shell, under brand palettes nobody would design against on purpose.
 *
 * **The design may not depend on any particular colour.** It was built while
 * looking at green, and everything looks deliberate in green. The second
 * tenant is a navy so dark it swallows a primary button, or a yellow so light
 * that white text on it is invisible, or a grey with so little chroma that
 * "primary" and "muted" become the same thing.
 *
 * So hierarchy has to come from type scale, weight, spacing and structure —
 * things a tenant cannot override — and these tests assert the parts of that
 * which are machine-checkable:
 *
 * - every derived pairing stays readable (the contrast suite proves this
 *   across the whole space; here it is asserted on the specific four);
 * - the accessibility tree is unchanged by the palette, because if a heading
 *   stops being a heading in yellow, the hierarchy was carried by colour;
 * - nothing in the shell hard-codes a colour that a tenant cannot influence.
 *
 * The visual comparison itself is a screenshot job, and screenshots are not
 * something a jsdom test can produce. What is asserted here is everything
 * that survives without a browser; `npm run screenshots` renders the four for
 * a human to look at.
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

function applyPalette(palette: (typeof HOSTILE_PALETTES)[number]): void {
  const tokens = buildTokens(palette);
  for (const [name, value] of Object.entries(tokens)) {
    document.documentElement.style.setProperty(name, value);
  }
}

describe.each(HOSTILE_PALETTES)("the shell under $name", (palette) => {
  it("keeps every derived pairing readable", () => {
    const tokens = buildTokens(palette);

    for (const name of ["primary", "secondary", "accent"]) {
      const background = parseHsl(tokens[`--${name}`]) as Hsl;
      const foreground = parseHsl(tokens[`--${name}-foreground`]) as Hsl;

      expect(contrastRatio(background, foreground)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("renders the shell with no accessibility violations", async () => {
    applyPalette(palette);
    const { container } = renderWithProviders(
      <main>
        <h1>Find a place near campus</h1>
        <p>Verified reviews from students who actually lived there.</p>
        <button type="button">Search</button>
      </main>,
      { withTenant: false },
    );

    expect(await axe(container)).toHaveNoViolations();
  });

  it("does not lose its heading structure", async () => {
    // If a heading stops reading as a heading under a hostile palette, the
    // hierarchy was being carried by colour rather than by structure -- which
    // is the failure this whole exercise exists to catch, and the one that
    // also breaks it for a screen reader in EVERY palette.
    applyPalette(palette);
    const { getByRole } = renderWithProviders(
      <main>
        <h1>Find a place near campus</h1>
        <h2>Near Kenyatta University</h2>
      </main>,
      { withTenant: false },
    );

    expect(getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(getByRole("heading", { level: 2 })).toBeInTheDocument();
  });
});

describe("the palettes themselves", () => {
  it("covers the four hazards that break a colour-dependent design", () => {
    // Named rather than counted, so adding a fifth is a decision and removing
    // one is visible in a diff.
    expect(HOSTILE_PALETTES.map((palette) => palette.name)).toEqual([
      "very dark navy",
      "very light yellow",
      "saturated red",
      "low-chroma grey",
    ]);
  });

  it("includes a palette at each end of the lightness range", () => {
    // A suite of four mid-tone brands would pass and prove nothing.
    const lightnesses = HOSTILE_PALETTES.map(
      (palette) => (parseHsl(palette.primary) as Hsl).l,
    );

    expect(Math.min(...lightnesses)).toBeLessThan(20);
    expect(Math.max(...lightnesses)).toBeGreaterThan(55);
  });

  it("includes a nearly-achromatic palette", () => {
    // The one that breaks "the primary colour draws the eye", which is the
    // assumption most likely to be baked into a layout without anyone
    // noticing they made it.
    const saturations = HOSTILE_PALETTES.map(
      (palette) => (parseHsl(palette.primary) as Hsl).s,
    );

    expect(Math.min(...saturations)).toBeLessThan(10);
  });

  it("states the hazard each one represents", () => {
    // A palette with no stated hazard is a colour somebody liked.
    for (const palette of HOSTILE_PALETTES) {
      expect(palette.hazard.length).toBeGreaterThan(20);
    }
  });
});
