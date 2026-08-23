/**
 * Design-token derivation for ADR-005.
 *
 * The university stores three HSL triples. Everything else is derived here,
 * because storing foregrounds separately invites a tenant configuration where
 * text is unreadable on its own background.
 */

export interface TenantTheme {
  primary: string;
  secondary: string;
  accent: string;
}

export interface Hsl {
  h: number;
  s: number;
  l: number;
}

/** shadcn's storage format: "142 71% 45%" — space separated, no hsl() wrapper. */
const HSL_PATTERN = /^(\d{1,3}(?:\.\d+)?)\s+(\d{1,3}(?:\.\d+)?)%\s+(\d{1,3}(?:\.\d+)?)%$/;

export function parseHsl(value: string): Hsl | null {
  const match = HSL_PATTERN.exec(value.trim());
  if (!match) return null;
  const [, h, s, l] = match;
  return { h: Number(h), s: Number(s), l: Number(l) };
}

export function formatHsl({ h, s, l }: Hsl): string {
  const round = (n: number) => Math.round(n * 100) / 100;
  return `${round(h)} ${round(s)}% ${round(l)}%`;
}

function clampLightness(l: number): number {
  return Math.min(100, Math.max(0, l));
}

export function lighten(hsl: Hsl, amount: number): Hsl {
  return { ...hsl, l: clampLightness(hsl.l + amount) };
}

export function darken(hsl: Hsl, amount: number): Hsl {
  return { ...hsl, l: clampLightness(hsl.l - amount) };
}

// ---------------------------------------------------------------------------
// Contrast
// ---------------------------------------------------------------------------

function hslToRgb({ h, s, l }: Hsl): [number, number, number] {
  const sat = s / 100;
  const lum = l / 100;
  const c = (1 - Math.abs(2 * lum - 1)) * sat;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = lum - c / 2;

  const [r, g, b] =
    h < 60
      ? [c, x, 0]
      : h < 120
        ? [x, c, 0]
        : h < 180
          ? [0, c, x]
          : h < 240
            ? [0, x, c]
            : h < 300
              ? [x, 0, c]
              : [c, 0, x];

  return [(r + m) * 255, (g + m) * 255, (b + m) * 255];
}

/** WCAG 2.1 relative luminance. */
export function relativeLuminance(hsl: Hsl): number {
  const channel = (value: number) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const [r, g, b] = hslToRgb(hsl);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: Hsl, b: Hsl): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [lighter, darker] = la > lb ? [la, lb] : [lb, la];
  return (lighter + 0.05) / (darker + 0.05);
}

const WHITE: Hsl = { h: 0, s: 0, l: 100 };
const BLACK: Hsl = { h: 0, s: 0, l: 0 };

/** Whichever of black or white reads better on `background`. */
export function readableForeground(background: Hsl): Hsl {
  return contrastRatio(background, WHITE) >= contrastRatio(background, BLACK) ? WHITE : BLACK;
}

// ---------------------------------------------------------------------------
// The token set ADR-005 writes to :root
// ---------------------------------------------------------------------------

/**
 * Exactly the variables ADR-005 permits: three overridden, four derived, two
 * adjusted. Backgrounds, borders and --destructive are deliberately absent —
 * a tenant must not be able to produce grey-on-grey, and a red-branded
 * university must not have its delete buttons blend into its primary ones.
 */
export function buildTokens(theme: TenantTheme, isDark = false): Record<string, string> {
  const tokens: Record<string, string> = {};

  const assign = (name: string, raw: string) => {
    const parsed = parseHsl(raw);
    if (!parsed) return;

    // A colour tuned for a white page is often too dark on a dark one; the
    // stock palette itself shifts primary from 45% to 50% lightness.
    const base = isDark ? lighten(parsed, 5) : parsed;

    tokens[`--${name}`] = formatHsl(base);
    tokens[`--${name}-foreground`] = formatHsl(readableForeground(base));

    if (name === "primary") {
      tokens["--primary-light"] = formatHsl(lighten(base, 10));
      tokens["--primary-dark"] = formatHsl(darken(base, 10));
      tokens["--ring"] = formatHsl(base);
    }
  };

  assign("primary", theme.primary);
  assign("secondary", theme.secondary);
  assign("accent", theme.accent);

  return tokens;
}

export function applyTokens(theme: TenantTheme, isDark = false): void {
  const root = document.documentElement;
  for (const [name, value] of Object.entries(buildTokens(theme, isDark))) {
    root.style.setProperty(name, value);
  }
}

export function clearTokens(): void {
  const root = document.documentElement;
  for (const name of [
    "--primary",
    "--primary-foreground",
    "--primary-light",
    "--primary-dark",
    "--ring",
    "--secondary",
    "--secondary-foreground",
    "--accent",
    "--accent-foreground",
  ]) {
    root.style.removeProperty(name);
  }
}
