import { describe, expect, it } from "vitest";

import luminance from "./photo-luminance.json";
import { AA_NORMAL, BLACK_OR_WHITE_CROSSOVER_LUMINANCE, BLACK_OR_WHITE_WORST_CASE } from "./contrast-floors";
import { HOSTILE_PALETTES } from "./hostile-palettes";
import { buildTokens, parseHsl, relativeLuminance, type Hsl } from "./tokens";

/**
 * The gallery arrows, over real photographs, measured.
 *
 * The last round reported that a control sitting on user-supplied imagery is
 * outside the palette suite's reach **by construction**, and reached that by
 * reasoning because measurement was impossible: jsdom has no pixels.
 *
 * It is possible now. `photo-luminance.json` holds the measured relative
 * luminance of the strip each arrow sits on, taken from the real photographs
 * the seed generates -- so Python owns the image and TypeScript owns the
 * palette, and neither reimplements the other.
 *
 * Contrast against a solid colour is `(lighter + 0.05) / (darker + 0.05)`.
 * The question these tests answer is: for each tenant palette, is there a
 * photograph the control disappears into?
 */

const PHOTOS = Object.entries(luminance) as Array<
  [string, { min: number; max: number; present: number[]; pixels: number }]
>;

function contrastAgainst(colour: Hsl, photoLuminance: number): number {
  const own = relativeLuminance(colour);
  const [lighter, darker] =
    own > photoLuminance ? [own, photoLuminance] : [photoLuminance, own];
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Every luminance that actually occurs under the control -- **pointwise**.
 *
 * The first version recorded min, mean and max over an approximated strip and
 * sampled evenly between the extremes. That is conservative rather than
 * narrow, since it tests values that may not be present, but it recorded a
 * `mean` nothing used and invited exactly the misreading it should not: a mean
 * of 0.179 could be half black and half white.
 *
 * So the fixture now records the set of 1/64 luminance buckets present in the
 * arrow's real footprint at both edges, from every pixel, and this iterates
 * those. What is checked is what is there.
 */
function luminanceRange(photo: { present: number[] }): number[] {
  return photo.present;
}

/** The worst any single flat colour does anywhere under the control. */
function worstCase(colour: Hsl, photo: { present: number[] }): number {
  return Math.min(...luminanceRange(photo).map((value) => contrastAgainst(colour, value)));
}

describe("the measurement the last round could only reason about", () => {
  it("has real photographs to measure against", () => {
    // A luminance file that quietly became empty would make every assertion
    // below vacuous.
    expect(PHOTOS.length).toBeGreaterThan(3);
    for (const [name, photo] of PHOTOS) {
      expect(photo.max).toBeGreaterThan(photo.min);
      // Measured per pixel, not summarised. A handful of buckets would mean
      // the footprint was nearly uniform and the test would be proving
      // something about a flat colour.
      expect(photo.present.length, `${name} has too few distinct luminances`).toBeGreaterThan(20);
      expect(photo.pixels, `${name} covers too few pixels`).toBeGreaterThan(1000);
    }
  });

  it.each(HOSTILE_PALETTES)(
    "$name: no single fill colour survives every photograph",
    (palette) => {
      // This is the claim, stated as a test rather than as a paragraph. For
      // every tenant colour there is a region of some real photograph that
      // the control's own fill dissolves into -- because a photograph
      // contains both a bright window and a dark doorway, and one flat colour
      // cannot contrast with both.
      const fill = parseHsl(buildTokens(palette)["--secondary"]) as Hsl;

      const worst = Math.min(...PHOTOS.map(([, photo]) => worstCase(fill, photo)));

      // Below AA. Somewhere in one of these photographs the control's own
      // fill is not distinguishable from what is behind it.
      expect(worst).toBeLessThan(AA_NORMAL);
    },
  );

  it("the border colour alone does not save it either", () => {
    // `--border` is a light grey, which is invisible against the bright half
    // of any photograph. The fix that shipped last round -- an opaque fill
    // plus a border in `--border` -- is better than 90% opacity and is still
    // one flat colour against an arbitrary background.
    const border: Hsl = { h: 0, s: 0, l: 90 };

    const worst = Math.min(...PHOTOS.map(([, photo]) => worstCase(border, photo)));

    expect(worst).toBeLessThan(AA_NORMAL);
  });

  it("a two-tone edge does survive, which is what the control needs", () => {
    // Black and white together. At **every** luminance, at least one of the
    // two contrasts -- and the worst point is not either extreme but the
    // middle, where both are weakest at once. That crossover sits at
    // L = 0.179 and gives 4.58:1, which is the same number
    // `contrast.test.ts` measures as the floor of the whole theming system.
    // It is not a coincidence: both are the crossover of "pick the better of
    // black and white".
    //
    // This is a property of the pair, not of the palette, which is why it
    // belongs in the component and not in the theme.
    const dark: Hsl = { h: 0, s: 0, l: 0 };
    const light: Hsl = { h: 0, s: 0, l: 100 };

    for (const [name, photo] of PHOTOS) {
      for (const value of luminanceRange(photo)) {
        const best = Math.max(contrastAgainst(dark, value), contrastAgainst(light, value));

        expect(best, `${name} at luminance ${value.toFixed(3)}`).toBeGreaterThanOrEqual(
          AA_NORMAL,
        );
      }
    }
  });

  it("names the worst luminance, so the number is not folk knowledge", () => {
    // The adversarial background for a black-and-white pair.
    const dark: Hsl = { h: 0, s: 0, l: 0 };
    const light: Hsl = { h: 0, s: 0, l: 100 };

    let worst = Infinity;
    let worstAt = 0;

    for (let value = 0; value <= 1; value += 0.001) {
      const best = Math.max(contrastAgainst(dark, value), contrastAgainst(light, value));
      if (best < worst) {
        worst = best;
        worstAt = value;
      }
    }

    expect(worstAt).toBeCloseTo(BLACK_OR_WHITE_CROSSOVER_LUMINANCE, 2);
    expect(worst).toBeGreaterThanOrEqual(AA_NORMAL);
    expect(worst).toBeLessThan(BLACK_OR_WHITE_WORST_CASE + 0.02);
  });
});
