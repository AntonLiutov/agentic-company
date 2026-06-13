---
name: web-app-aesthetics
description: Make every generated web UI look premium and intentional, in the spirit of Linear / Vercel / Stripe / Raycast. Use for any frontend build or UI repair to set styling, theme, layout, components, and motion. Do not use for QA-only validation, deployment-only work, or release reporting.
---

# Web App Aesthetics

## Purpose

Ship UIs that look hand-designed, not generated. Apply the "Quiet Console"
signature below as the default look for every web app, and refuse the documented
AI/Bootstrap "slop" tells. Style is part of the deliverable, not an afterthought.

## Signature — "Quiet Console"

Dark-first, near-monochrome, one accent. Six rules:

1. **Near-monochrome neutral ladder; one accent used only for meaning** (primary
   action, focus ring, active nav, selection) — never as decoration or a gradient.
2. **Never pure `#000`/`#fff`.** Off-black canvas, off-white ink (kills halation).
3. **Depth from a surface ladder + 1px hairline borders, almost no shadow.**
4. **Strict base-4 spacing, generous.** One radius family (8px controls, 10–12px cards).
5. **Tight high-contrast type** (negative letter-spacing on large text) + small
   UPPERCASE tracked eyebrow labels — the premium tell.
6. **Real states + real empty/loading/error.** No lorem, no emoji-as-icons.

## Directions — pick ONE by product type

The tokens below are the **Quiet Console** default. For a different product, switch
only these few variables; everything else (spacing, hairline depth, states, the
Forbidden list) stays identical. Choose by product type and never mix two directions.

- **Quiet Console** (default) — dashboards, dev tools, admin, analytics, anything
  technical. Dark-first, cool neutrals, accent `oklch(0.62 0.19 264)`, radius 8/12.
- **Editorial Light** — landing/marketing, docs, blogs, reading. Light-first
  (`color-scheme: light`, warm paper `--bg:#fbfaf8`, ink `#1a1916`), one confident warm
  accent `--accent: oklch(0.55 0.20 28)` (terracotta) or deep green; larger type scale,
  96–128px section rhythm; an optional display serif for headings only.
- **Soft Product** — friendly consumer apps (notes, todo, social). Warmer neutrals,
  slightly larger radius (`--r:10px; --r-card:16px`), one calm accent like teal
  `oklch(0.70 0.13 195)` or coral `oklch(0.70 0.16 30)` — never violet/indigo; a touch
  more whitespace and rounding.

## Design tokens (ship this foundation, then build on it)

Copy these into the app's CSS first; re-skin the whole app by editing a handful of
variables. Authored in OKLCH (perceptually uniform); keep hex equivalents if a
target needs them.

```css
:root {
  color-scheme: dark;

  /* NEUTRAL LADDER (dark-first) — fallback hex in comments */
  --bg:            oklch(0.16 0.005 270);  /* #0c0d0f canvas     */
  --surface:       oklch(0.19 0.006 270);  /* #141516 card       */
  --surface-2:     oklch(0.22 0.006 270);  /* #1a1b1d elevated   */
  --hairline:      oklch(0.27 0.006 270);  /* #26282b 1px border */
  --hairline-soft: rgba(255,255,255,0.07);

  /* TEXT — off-white, three levels of presence */
  --ink:        oklch(0.97 0.003 270);     /* #f5f6f7 primary    */
  --ink-muted:  oklch(0.78 0.010 270);     /* #b6bac0 secondary  */
  --ink-subtle: oklch(0.62 0.012 270);     /* #8a8f98 labels     */

  /* ACCENT — ONE hue (primary CTA / focus / active only). Swap to re-skin. */
  --accent:        oklch(0.62 0.19 264);   /* ~#5e6ad2           */
  --accent-hover:  oklch(0.68 0.18 264);
  --on-accent:     #ffffff;
  --accent-subtle: oklch(0.62 0.19 264 / 0.14);

  /* SEMANTIC (status only, desaturated) */
  --ok:   oklch(0.72 0.15 150);
  --warn: oklch(0.78 0.14 75);
  --err:  oklch(0.66 0.20 22);

  /* RADIUS — one family */
  --r-sm: 6px; --r: 8px; --r-card: 12px; --r-pill: 9999px;

  /* SPACING — base-4 only */
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s6:24px;
  --s8:32px; --s12:48px; --s16:64px; --s24:96px;

  /* TYPE — one sans + mono; tighten big, track small labels */
  --font: "Inter","Geist",system-ui,-apple-system,sans-serif;
  --mono: "Geist Mono","JetBrains Mono",ui-monospace,monospace;

  /* MOTION — designed, not defaulted */
  --dur-press: 90ms; --dur-fast: 140ms; --dur-enter: 180ms;
  --ease-out:      cubic-bezier(0.22, 1, 0.36, 1);
  --ease-standard: cubic-bezier(0.4, 0, 0.2, 1);

  /* SHADOW — sparingly; light mode only; tight + stacked */
  --shadow: 0 1px 2px rgb(16 24 40 / .06), 0 4px 8px -2px rgb(16 24 40 / .06);

  /* WIDTH regimes */
  --w-app: 1440px; --w-marketing: 1120px; --w-prose: 68ch; --sidebar: 256px;
  --gutter: clamp(16px, 5vw, 32px);
}

[data-theme="light"] {
  color-scheme: light;
  --bg: #ffffff; --surface: #fafafa; --surface-2: #f5f5f5;
  --hairline: #ebebeb; --hairline-soft: rgb(16 24 40 / .06);
  --ink: #171717; --ink-muted: #4d4d4d; --ink-subtle: #888888;
}

* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 var(--font); font-feature-settings: "calt","kern","liga";
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }

/* Headings: tighten + weight 500–600 (never 700+ everywhere) */
h1 { font-size: clamp(2rem, 1.4rem + 3vw, 3.25rem); font-weight: 600;
     line-height: 1.08; letter-spacing: -0.03em; margin: 0; }
h2 { font-size: clamp(1.5rem, 1.2rem + 1vw, 2rem); font-weight: 600;
     letter-spacing: -0.02em; margin: 0; }

/* The premium tell: uppercase, tracked, muted micro-label */
.eyebrow { font-size: 12px; font-weight: 500; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--ink-subtle); }

/* Tables/metrics: tabular numbers so digits align */
.nums, td.num, [data-numeric] { font-variant-numeric: tabular-nums; }
td.num { text-align: right; }

/* Card — depth from surface + hairline, not a fat shadow */
.card { background: var(--surface); border: 1px solid var(--hairline);
  border-radius: var(--r-card); padding: var(--s6); }

/* Primary button — the ONE place accent appears; full states */
.btn { display: inline-flex; align-items: center; gap: var(--s2);
  padding: 8px 16px; border: 1px solid transparent; border-radius: var(--r);
  background: var(--accent); color: var(--on-accent);
  font: 500 14px/1 var(--font); cursor: pointer;
  transition: background var(--dur-fast) var(--ease-standard),
              transform var(--dur-press) var(--ease-out); }
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { background: var(--accent-hover); transform: translateY(-1px); }
}
.btn:active:not(:disabled) { transform: translateY(0) scale(0.98); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
/* Secondary = ghost/hairline, never a second accent color */
.btn-ghost { background: transparent; color: var(--ink); border-color: var(--hairline); }
.btn-ghost:hover:not(:disabled) { background: var(--surface-2); }

/* Input — focus ring via box-shadow (animates smoothly) */
.input { width: 100%; padding: 9px 12px; background: var(--surface);
  color: var(--ink); border: 1px solid var(--hairline); border-radius: var(--r);
  transition: border-color var(--dur-fast) var(--ease-standard),
              box-shadow var(--dur-fast) var(--ease-standard); }
.input:focus-visible { outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-subtle); }

/* Focus — replace, never just remove, the default ring */
:where(a,button,input,select,textarea):focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }

/* Reduced motion — neutralize transforms, keep fades. Place LAST. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important;
    transition-duration: .01ms !important; scroll-behavior: auto !important; }
  .btn:hover, .btn:active { transform: none !important; }
}
```

## Layout and composition

- **Three width regimes, never one global container:** app/dashboard caps at
  `--w-app` (sidebar + fluid content), marketing at `--w-marketing`, prose at
  `--w-prose` (~68ch). Never let body text run full viewport width.
- **One card recipe reused everywhere** (same radius, padding, hairline). Separate
  groups with whitespace, not boxes-in-boxes.
- **Hierarchy: one dominant element per view** (a hero metric / primary chart / H1)
  at ~2–3× the weight of the rest — not a flat grid of equal cards. For dashboards
  use a 12-col grid with size contrast (bento), not rows of identical tiles.
- **Data UIs are dense and scannable:** sticky solid table header, 44–52px rows,
  right-aligned tabular numerics.
- **Responsive: mobile-first, ~3 breakpoints** (640/768/1024). Size headings with
  `clamp()` so type scales continuously; body stays fixed at 15–16px.

## Motion and states

- Timing: hover/color 120–160ms, press 80–120ms, enter 150–200ms. Never
  `transition: all`; name properties.
- Animate only `transform` and `opacity` (a 1–2px lift, a `scale(0.98)` press).
- Every interactive element ships **default, hover, focus-visible, active,
  disabled** (and loading where relevant). Gate hover with `@media (hover: hover)`.
- Skeletons mirror the final layout; empty states get an icon + one line + one CTA.

## Forbidden — the AI/Bootstrap "slop" tells

- The **indigo/violet → blue gradient hero** (and gradient-filled headline/metric
  text). The #1 tell. Also forbid `#0d6efd` Bootstrap blue and `#0f172a` slate canvas.
- **Everything centered** (centered hero + CTA + three identical icon-boxes in a row).
- **Rows of identical equal-weight cards** as the primary layout.
- **Glassmorphism everywhere**, neon glows, blurred orbs, and a thick colored
  border on one side of a card.
- A **single heavy/blurry shadow** (`0 10px 30px rgba(0,0,0,.1)`) on everything.
- **Pure black/white** in dark mode; **inconsistent radii**; **cramped, unpadded cards**.
- **Emoji as UI icons**; mixing two icon styles. Use one line-icon set (Lucide/Phosphor).
- **Accent sprayed everywhere**; 12 font sizes; weight 700+ on everything.
- **Lorem ipsum / missing states** (no empty/loading/error UI).

## Workflow

1. If the project has no design foundation, add the token block above and wire a
   `[data-theme]` toggle; if it has one, extend it — do not fork a second system.
2. Build the work item's UI from the tokens and the one card/button/input recipe.
3. Apply the signature: neutral surfaces, one accent, eyebrow labels, tabular nums.
4. Add real hover/focus/active/disabled states and real empty/loading/error states.
5. Self-check against the Forbidden list before returning.

## Quality Rules

- Reference tokens for every color/space/radius — zero hardcoded hex or magic px.
- Keep the single accent rationed; keep one consistent radius family.
- Preserve an existing project's aesthetic; refine toward the signature, don't rewrite.
- Verify both dark and light render correctly.

## Examples

### Good invocation

Input: Build the task board UI for this work item.
Output: Tokenized dark-first UI, one accent on the primary action, hairline cards,
eyebrow labels, tabular numbers, real hover/empty states — no purple gradient.

### Bad invocation

Input: Build the dashboard.
Output: Centered hero with an indigo→purple gradient, three identical icon cards,
emoji icons, heavy drop shadows, and no empty/loading states.
