import { describe, expect, it } from "vitest";

import { buildTokens, contrastRatio, formatHsl, parseHsl, readableForeground } from "./tokens";

describe("HSL parsing", () => {
  it("accepts shadcn's space-separated format", () => {
    expect(parseHsl("142 71% 45%")).toEqual({ h: 142, s: 71, l: 45 });
    expect(parseHsl("  210 90.5% 40%  ")).toEqual({ h: 210, s: 90.5, l: 40 });
  });

  it("rejects anything else", () => {
    // A wrapped or comma-separated value would break `hsl(var(--primary) / 0.5)`.
    for (const bad of ["hsl(142 71% 45%)", "142, 71%, 45%", "#22c55e", "142 71 45", ""]) {
      expect(parseHsl(bad)).toBeNull();
    }
  });

  it("round-trips through formatHsl", () => {
    expect(formatHsl(parseHsl("142 71% 45%")!)).toBe("142 71% 45%");
  });
});

describe("contrast derivation", () => {
  it("puts white on a dark background and black on a light one", () => {
    expect(readableForeground({ h: 220, s: 80, l: 20 })).toEqual({ h: 0, s: 0, l: 100 });
    expect(readableForeground({ h: 50, s: 90, l: 85 })).toEqual({ h: 0, s: 0, l: 0 });
  });

  it("always clears the WCAG AA large-text threshold", () => {
    // Derivation exists so a tenant cannot configure an unreadable button.
    for (let h = 0; h < 360; h += 15) {
      for (const l of [20, 35, 45, 55, 70, 85]) {
        const background = { h, s: 75, l };
        const ratio = contrastRatio(background, readableForeground(background));
        expect(ratio).toBeGreaterThanOrEqual(3);
      }
    }
  });
});

describe("buildTokens", () => {
  const theme = { primary: "210 90% 40%", secondary: "30 50% 40%", accent: "210 90% 95%" };

  it("emits exactly the tokens ADR-005 permits", () => {
    expect(Object.keys(buildTokens(theme)).sort()).toEqual([
      "--accent",
      "--accent-foreground",
      "--primary",
      "--primary-dark",
      "--primary-foreground",
      "--primary-light",
      "--ring",
      "--secondary",
      "--secondary-foreground",
    ]);
  });

  it("never touches backgrounds, borders or --destructive", () => {
    const keys = Object.keys(buildTokens(theme));
    // A tenant must not be able to produce grey-on-grey, and a red-branded
    // university must not have delete buttons matching its primary.
    for (const forbidden of [
      "--background",
      "--foreground",
      "--card",
      "--muted",
      "--border",
      "--input",
      "--destructive",
      "--radius",
    ]) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it("ties --ring to --primary", () => {
    const tokens = buildTokens(theme);
    expect(tokens["--ring"]).toBe(tokens["--primary"]);
  });

  it("derives light and dark primary variants", () => {
    const tokens = buildTokens(theme);
    expect(tokens["--primary-light"]).toBe("210 90% 50%");
    expect(tokens["--primary-dark"]).toBe("210 90% 30%");
  });

  it("lifts lightness for dark mode", () => {
    expect(buildTokens(theme, true)["--primary"]).toBe("210 90% 45%");
  });

  it("skips a malformed value rather than emitting garbage", () => {
    const tokens = buildTokens({ ...theme, secondary: "not-a-colour" });
    expect(tokens["--primary"]).toBeDefined();
    expect(tokens["--secondary"]).toBeUndefined();
  });

  it("clamps derived lightness at the extremes", () => {
    const tokens = buildTokens({ ...theme, primary: "0 0% 97%" });
    expect(tokens["--primary-light"]).toBe("0 0% 100%");
  });
});
