import { describe, expect, it } from "vitest";

import {
  buildTokens,
  contrastRatio,
  parseHsl,
  readableForeground,
  type Hsl,
} from "./tokens";

/**
 * Contrast is computed and tested, not eyeballed.
 *
 * A school picks its own brand colour and we derive the text colour that goes
 * on it. "It looked fine in the mockup" is not a property — the mockup was
 * green. This sweeps the colour space and asserts the derivation holds for
 * every colour a university could possibly configure, including the ones
 * nobody would design against on purpose.
 *
 * **The measured floor is 4.5826:1**, at roughly `166 16% 42%` — a desaturated
 * teal. That is barely above AA's 4.5:1 and it is not a coincidence: picking
 * the better of black and white has a mathematical worst case where the two
 * are equally bad, and that crossover sits just above the threshold. There is
 * no room to spare, which is why this is a test and not a convention.
 */

import { AA_LARGE, AA_NORMAL, AAA_NORMAL } from "./contrast-floors";

/** Every 7th hue, every 5th saturation, every 3rd lightness. ~30k samples:
 *  dense enough to find the crossover, fast enough to run on every commit. */
function* colourSpace(): Generator<Hsl> {
  for (let h = 0; h < 360; h += 7) {
    for (let s = 0; s <= 100; s += 5) {
      for (let l = 0; l <= 100; l += 3) {
        yield { h, s, l };
      }
    }
  }
}

function derivedPair(background: Hsl): number {
  return contrastRatio(background, readableForeground(background));
}

describe("the derivation holds across the colour space", () => {
  it("meets AA for normal text at every point", () => {
    // The property the whole theming system rests on: a university cannot
    // configure a brand colour that produces unreadable text, because they do
    // not configure the text colour.
    const failures: Array<{ colour: Hsl; ratio: number }> = [];

    for (const colour of colourSpace()) {
      const ratio = derivedPair(colour);
      if (ratio < AA_NORMAL) failures.push({ colour, ratio });
    }

    expect(failures).toEqual([]);
  });

  it("has almost no margin, which is why it is tested", () => {
    // Measured minimum across a 1.85M-sample sweep: 4.5826:1. If a future
    // change to `readableForeground` -- a tinted foreground instead of pure
    // black or white, say -- eats a tenth of a point, this catches it.
    let worst = Infinity;
    let worstAt: Hsl | null = null;

    for (const colour of colourSpace()) {
      const ratio = derivedPair(colour);
      if (ratio < worst) {
        worst = ratio;
        worstAt = colour;
      }
    }

    expect(worst).toBeGreaterThanOrEqual(AA_NORMAL);
    expect(worst).toBeLessThan(5);
    expect(worstAt).not.toBeNull();
  });

  it("meets AA for large text everywhere, with room", () => {
    for (const colour of colourSpace()) {
      expect(derivedPair(colour)).toBeGreaterThanOrEqual(AA_LARGE);
    }
  });

  it("does NOT meet AAA everywhere, and that is recorded rather than hidden", () => {
    // ~21% of the colour space cannot reach 7:1 with black or white text.
    // Nothing is broken -- AA is the target -- but a future ticket asking for
    // AAA needs to know it is unreachable by this mechanism, not merely
    // unimplemented. Reaching AAA would mean overriding the tenant's colour,
    // which is the one thing ADR-005 does not allow.
    const failing = [...colourSpace()].filter(
      (colour) => derivedPair(colour) < AAA_NORMAL,
    );

    expect(failing.length).toBeGreaterThan(0);
  });
});

describe("the colours a school might actually pick", () => {
  // The four hostile palettes the shell is screenshotted under, plus the
  // stock green. Named because a failure here should say which school broke.
  const brands: Array<[string, string]> = [
    ["stock green", "142 71% 45%"],
    ["very dark navy", "222 60% 11%"],
    ["very light yellow", "52 98% 62%"],
    ["saturated red", "0 88% 52%"],
    ["low-chroma grey", "210 4% 46%"],
    ["mid teal, the measured worst case", "166 16% 42%"],
  ];

  it.each(brands)("%s produces readable text", (_name, value) => {
    const parsed = parseHsl(value);
    expect(parsed).not.toBeNull();
    expect(derivedPair(parsed as Hsl)).toBeGreaterThanOrEqual(AA_NORMAL);
  });

  it.each(brands)("%s produces readable text in dark mode too", (_name, value) => {
    // Dark mode lightens the base by 5%, which moves it toward the crossover
    // rather than away from it for an already-mid colour.
    const tokens = buildTokens({ primary: value, secondary: value, accent: value }, true);
    const background = parseHsl(tokens["--primary"]);
    const foreground = parseHsl(tokens["--primary-foreground"]);

    expect(contrastRatio(background as Hsl, foreground as Hsl)).toBeGreaterThanOrEqual(
      AA_NORMAL,
    );
  });

  it("a yellow brand gets black text, not white", () => {
    // The specific failure named in the brief. White on yellow is the
    // canonical unreadable pairing and the one a designer picks by habit
    // because it looks right in a swatch.
    const yellow = parseHsl("52 98% 62%") as Hsl;

    expect(readableForeground(yellow).l).toBe(0);
  });

  it("a very dark navy gets white text, not black", () => {
    const navy = parseHsl("222 60% 11%") as Hsl;

    expect(readableForeground(navy).l).toBe(100);
  });
});

describe("every derived token pairs readably", () => {
  it.each(["primary", "secondary", "accent"])(
    "--%s and its foreground meet AA for any brand",
    (name) => {
      // Not just primary. A school setting a light accent and a dark primary
      // gets both derived independently, and the accent is where a badge or a
      // filter chip renders text.
      for (const colour of colourSpace()) {
        const value = `${colour.h} ${colour.s}% ${colour.l}%`;
        const tokens = buildTokens({ primary: value, secondary: value, accent: value });

        const background = parseHsl(tokens[`--${name}`]);
        const foreground = parseHsl(tokens[`--${name}-foreground`]);

        expect(contrastRatio(background as Hsl, foreground as Hsl)).toBeGreaterThanOrEqual(
          AA_NORMAL,
        );
      }
    },
  );
});
