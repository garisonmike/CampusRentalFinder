# ADR-005: Per-university theming through database-stored design tokens

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — gradient tokens rewritten to reference var(--primary)
**Deciders:** Tech lead

## Context

The platform is sold to universities on the promise that it looks like *their*
service. Today it does not: `frontend/src/index.css` hard-codes a single green
(`--primary: 142 71% 45%`) for every institution, and the only way to change it
is a rebuild and a redeploy.

Rebuilding per tenant is not viable — N universities would mean N build
artefacts, N deploys, and a colour change becoming an engineering ticket.

The useful accident is that the existing frontend is already built on
shadcn/ui, whose entire colour system reads CSS custom properties from `:root`
as raw HSL triples, wired through Tailwind as `hsl(var(--token))`. The
substrate for runtime theming is already in place; nothing is using it.

## Decision

**The `University` model stores design tokens. A public endpoint serves them.
The React app applies them to `:root` before first paint.**

### Storage

```
University
├── name, display_name      "Kenyatta University" / "KyU"
├── subdomain               "kyu"  → kyu.example.co.ke   (unique, indexed)
├── domain                  "ku.ac.ke"
├── email_domains           ["students.ku.ac.ke", "ku.ac.ke"]  (ADR-003)
├── logo_url                URL on the media CDN (ADR-007)
├── favicon_url
├── primary_hsl             "142 71% 45%"    ← space-separated HSL, no hsl()
├── secondary_hsl           "30 50% 40%"
├── accent_hsl              "142 71% 95%"
├── is_active
└── created_at, updated_at
```

Colours are stored **exactly as shadcn expects them**: three space-separated
components, no `hsl()` wrapper, no commas. That is what lets
`hsl(var(--primary) / 0.5)` work for opacity variants, and it means the value
goes from the database into the stylesheet with no parsing.

Validated on save against `^\d{1,3}(\.\d+)?\s+\d{1,3}(\.\d+)?%\s+\d{1,3}(\.\d+)?%$`.

### Delivery

`GET /api/v1/tenant/config/` — **unauthenticated**, resolved from the subdomain
by the ADR-001 middleware, cached hard:

```json
{
  "subdomain": "kyu",
  "name": "Kenyatta University",
  "display_name": "KyU",
  "logo_url": "https://media.example.co.ke/universities/kyu/logo.svg",
  "favicon_url": "https://media.example.co.ke/universities/kyu/favicon.png",
  "theme": {
    "primary": "142 71% 45%",
    "secondary": "30 50% 40%",
    "accent": "142 71% 95%"
  }
}
```

It must be unauthenticated: the login page itself has to be branded, and it
renders before any token exists.

### Application

The React entry point fetches this **before rendering the tree** and writes the
tokens onto `document.documentElement`:

```ts
const root = document.documentElement;
root.style.setProperty("--primary", config.theme.primary);
root.style.setProperty("--secondary", config.theme.secondary);
root.style.setProperty("--accent", config.theme.accent);
```

Inline styles on `:root` beat the stylesheet's `:root` rule by specificity, so
the values in `index.css` become defaults and the fetched ones win.

## Exactly which CSS variables are overridden

`index.css` defines 26 tokens. **This decision overrides three, and derives four
more.** Everything else is deliberately untouched.

### Overridden directly (3)

| Variable | Source | Effect |
|---|---|---|
| `--primary` | `University.primary_hsl` | Every primary button, link hover, focus ring source, active nav item, `bg-primary`, `text-primary`, `border-primary` |
| `--secondary` | `University.secondary_hsl` | Secondary buttons and badges, `bg-secondary` |
| `--accent` | `University.accent_hsl` | Hover backgrounds on menu items, dropdowns, and command palette rows |

### Derived from the above (4)

These are computed client-side from the three stored values, because storing
them separately invites a tenant configuration where text is unreadable on its
own background:

| Variable | Derivation |
|---|---|
| `--primary-foreground` | Black or white by WCAG contrast against `--primary` |
| `--secondary-foreground` | Same, against `--secondary` |
| `--accent-foreground` | Same, against `--accent` |
| `--ring` | Set equal to `--primary` — the focus ring should follow brand colour |

### Also updated (2, project-specific extensions)

| Variable | Derivation |
|---|---|
| `--primary-light` | `--primary` with lightness raised ~10 percentage points |
| `--primary-dark` | `--primary` with lightness lowered ~10 percentage points |

`tailwind.config.ts` already maps these to `primary.light` / `primary.dark`.

### Deliberately NOT overridden (17)

`--background`, `--foreground`, `--card`, `--card-foreground`, `--popover`,
`--popover-foreground`, `--muted`, `--muted-foreground`, `--destructive`,
`--destructive-foreground`, `--border`, `--input`, `--radius`, and the four
`--shadow-*` values.

Two reasons. **Legibility:** a tenant that sets its own `--background` and
`--foreground` can produce grey-on-grey, and no amount of contrast-checking on
our side prevents a determined brand guideline from doing it. **Semantics:**
`--destructive` means "this deletes something". A university whose brand colour
happens to be red must not have its delete buttons blend into its primary
buttons.

### The two gradient tokens — RESOLVED

Design review found that `--gradient-hero` and `--gradient-card` hard-code the
stock green as a literal rather than referencing `--primary`:

```css
--gradient-hero: linear-gradient(135deg, hsl(142 71% 45%) 0%, hsl(142 71% 35%) 100%);
--gradient-card: linear-gradient(180deg, hsl(0 0% 100%) 0%, hsl(142 71% 98%) 100%);
```

Left as they are, the hero section and card backgrounds would stay green on
every tenant while everything around them rebranded — the most visible surface
on the page being the one that ignores the university's colour.

**Resolved: both are rewritten to reference the token.**

```css
--gradient-hero: linear-gradient(
  135deg,
  hsl(var(--primary)) 0%,
  hsl(var(--primary-dark)) 100%
);
--gradient-card: linear-gradient(
  180deg,
  hsl(var(--card)) 0%,
  hsl(var(--primary) / 0.04) 100%
);
```

`--gradient-card` uses `--card` for its start rather than a literal white, so it
also survives dark mode, and the primary tint is applied at 4% opacity through
the slash syntax that space-separated HSL components make possible. This is the
one change to existing CSS the decision requires, and it is what makes the
opacity modifier worth insisting on in the storage format.

### Dark mode

`.dark` in `index.css` redefines the same tokens. Inline styles on
`documentElement` override both `:root` and `.dark`, so **a tenant's primary
colour will be applied unchanged in dark mode** — where a colour tuned for a
white background may be too dark to read. The applier adjusts lightness for the
dark theme (raising L by ~5 points, as the stock palette already does: light
`142 71% 45%` → dark `142 71% 50%`) and re-runs the contrast derivation.

## Rule: the background is not ours to theme, so neither is the edge

**Any element composited over user-supplied imagery must be legible by
construction, not by token contrast.**

This is a rule rather than a note because the reasoning behind every contrast
guarantee in this project stops exactly at the edge of a photograph. The
derivation proves that a tenant's colour pairs readably with the foreground we
derive *for it*; `theme/contrast.test.ts` sweeps ~30,000 colours to prove it.
None of that says anything about a control sitting on a landlord's photograph,
because no palette computation can make a claim about an image nobody has seen.

Measured, not asserted. `theme/gallery-over-photos.test.ts` reads the
per-pixel luminances actually present under the gallery arrows in real
photographs and finds that **every** tenant fill drops below AA somewhere in an
ordinary photo — 1.69:1 for the dark navy, 2.61:1 for the low-chroma grey,
3.87:1 for the saturated red. So does `--border`. A photograph contains a
bright window and a dark doorway; no single colour contrasts with both.

A black-and-white pair does, and its worst case is the crossover where both are
weakest at once: L = 0.179, giving **4.58:1** — the same number the tenant
derivation floors at, because both are the crossover of "pick the better of
black and white". It is declared once in `theme/contrast-floors.ts`.

### What this means in practice

- An element over imagery uses a **two-tone edge**, an **opaque scrim**, or
  both. One flat colour, however chosen, is not enough.
- Those colours are **deliberately not tokens**. A tenant cannot theme what
  sits behind them, so letting a tenant theme what sits in front is how the
  guarantee is lost.
- The palette suite's silence about these elements is **correct behaviour**,
  not a gap to close later. Reading that silence as approval is the mistake
  (`docs/OPERATIONS.md`, "checks whose scope is narrower than the belief
  attached to them").

### Where it currently applies

The gallery arrows, and nothing else — the audit that produced this rule found
no price badge, verified badge, image counter, favourite control, gradient
scrim or text rendered over a listing photo anywhere in the interface. Every
other overlay candidate sits on the card background, which **is** ours to
theme. That is worth recording, because the next such element will be added by
somebody who has not read this, and the rule is what they need to meet.

## Consequences

### What this buys us

- **No component changes.** Every shadcn component already reads these tokens.
  A new university is three colour values in a database row.
- **No rebuild, no redeploy** to change branding. Support can do it.
- One build artefact serves every tenant, so CDN caching stays simple.
- Contrast is derived rather than configured, so a tenant cannot ship an
  unreadable button.

### What it costs us

- **A blocking fetch before first paint.** This is the significant cost: the app
  cannot render until the config arrives, so a slow network shows a blank page
  rather than an unbranded one. Mitigations: cache the response in
  `localStorage` keyed by subdomain and paint immediately from the cached copy
  while revalidating; serve the endpoint from cache with a long `max-age`; keep
  the payload under 1 KB.
- **A flash of unthemed content if the fetch is not blocking**, and a flash of
  *stale* branding if it is cached. The `localStorage`-then-revalidate approach
  trades the first for the second, which is the better trade — stale-by-seconds
  brand colour is invisible, a white flash is not.
- **The endpoint is a hard dependency of the first paint.** If
  `/tenant/config/` is down, the app is down. It must be cached aggressively,
  and the client must fall back to the stylesheet defaults rather than erroring.
- **Inline styles on `documentElement` are invisible in the source CSS.** A
  developer debugging a colour will find `--primary: 142 71% 45%` in
  `index.css` and a different computed value in the browser. Document it at the
  top of `index.css`, or it will cost somebody an afternoon.
- **CSS variables are not available to the `<meta name="theme-color">` tag** or
  to the favicon, so mobile browser chrome and the tab icon need separate
  handling from the same config payload.

### Open, and not blocking: three colours may not satisfy a brand team

**Three colours is probably not enough to make a university's brand team say
yes.** Institutional identity usually involves a specific typeface, a logo with
clear-space rules, and often a defined tint ramp — not a hue. A tenant can end
up with our layout in their green, which reads as a skin rather than as their
service.

That may well be acceptable for the first sales conversations, and the decision
is right to start small — every additional token is another way to break the
UI. But expect the second or third university to ask for a font. The token
architecture extends to that cleanly (`--font-sans` as a custom property, with
the font file served from the same media bucket as the logo), so the path is
open; it is just not in scope here, and the sales conversation should not
promise it yet.

Secondly, `logo_url` as a bare URL puts no constraint on aspect ratio or
format. A university that supplies a 2000×400 PNG will break the navbar. Store
intended dimensions alongside, or validate on upload.

## Alternatives considered

### Build-time theming, one bundle per tenant — rejected

Tailwind can compile a palette per tenant. N build artefacts, N deploys, and a
colour change becomes an engineering ticket with a release cycle. Defeats the
purpose.

### A per-tenant stylesheet served from the API (`<link href="/tenant/theme.css">`) — rejected, though close

Genuinely good: the browser blocks on stylesheets anyway, so there is no
unstyled flash, and no JavaScript is involved. Rejected because the same
endpoint must also carry `display_name`, `logo_url` and `favicon_url`, which are
not CSS — so we would maintain two endpoints and two caches for one concept.
**Worth revisiting if the flash-of-unthemed-content proves visible in
practice**; it is the cleanest fix for that specific problem.

### CSS-in-JS with a theme provider — rejected

Would mean abandoning the shadcn/Tailwind token system the frontend is already
built on, rewriting every component, and adding a runtime styling cost. The
existing architecture is the reason this ADR is cheap.

### Storing colours as hex and converting server-side — rejected

Designers supply hex, so this is friendlier at the input boundary. But shadcn
needs HSL components for its opacity modifiers (`hsl(var(--primary) / 0.5)`),
so a conversion would happen somewhere regardless. Convert in the **admin form**
— accept hex from the user, store HSL — and keep the storage format identical
to the consumption format.
