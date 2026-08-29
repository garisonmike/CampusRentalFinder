/**
 * The contrast thresholds, declared once.
 *
 * `4.5` was written as a bare literal in three separate test files -- six
 * times in total, with only one of them named. They agreed, which is what made
 * it worth fixing rather than urgent: a value repeated six times is a value
 * that will eventually be raised in five places. `docs/OPERATIONS.md` collects
 * that shape, and this is its eighth instance.
 *
 * The numbers are WCAG's, not ours, which is precisely why they belong in one
 * place: if a future standard moves them, the change is one line rather than a
 * search.
 */

/** WCAG AA, normal text. The floor everything in this project is held to. */
export const AA_NORMAL = 4.5;

/** WCAG AA, large text (18pt, or 14pt bold). */
export const AA_LARGE = 3;

/** WCAG AAA, normal text. Unreachable for ~21% of the colour space by the
 *  black-or-white derivation, which `contrast.test.ts` records deliberately. */
export const AAA_NORMAL = 7;

/**
 * The worst case of picking the better of black and white against any
 * background: 4.58:1, at a background luminance of 0.179.
 *
 * It appears twice for two different reasons and they are the same number.
 * `contrast.test.ts` measures it as the floor of the tenant-palette
 * derivation; `gallery-over-photos.test.ts` derives it as the guarantee a
 * black-and-white edge gives over an arbitrary photograph. Both are the
 * crossover of the same choice, which is why one constant serves both.
 */
export const BLACK_OR_WHITE_WORST_CASE = 4.58;

/** Where that crossover sits, as a relative luminance. */
export const BLACK_OR_WHITE_CROSSOVER_LUMINANCE = 0.179;
