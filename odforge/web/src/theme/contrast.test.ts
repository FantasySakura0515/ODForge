import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

/**
 * The design tokens are checked, not eyeballed.
 *
 * `--quiet` was 2.4:1 on a light card and `--warn` was 4.25:1 on the page
 * background — both used for status text a user has to read in order to know
 * what went wrong. Nobody noticed because contrast is exactly the kind of thing
 * that looks fine to the person who picked the colour. This test parses
 * tokens.css and does the arithmetic, so a regression is a red build rather than
 * a bug report from someone who could not read the screen.
 */

// Read from the project root rather than import.meta.url: vitest serves test
// modules over an http: URL, so fileURLToPath cannot resolve a sibling file.
const CSS = readFileSync(join(process.cwd(), "src/theme/tokens.css"), "utf8");

function relativeLuminance(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16) / 255);
  const linear = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(fg: string, bg: string): number {
  const a = relativeLuminance(fg);
  const b = relativeLuminance(bg);
  const [hi, lo] = a >= b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/** Read one theme block's `--name: #hex;` declarations. */
function tokensFrom(selector: string): Record<string, string> {
  const start = CSS.indexOf(selector);
  expect(start, `theme block not found: ${selector}`).toBeGreaterThan(-1);
  const open = CSS.indexOf("{", start);
  // The blocks are flat (no nesting), so the first `}` closes them.
  const block = CSS.slice(open + 1, CSS.indexOf("}", open));
  const tokens: Record<string, string> = {};
  for (const [, name, hex] of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens[name] = hex;
  }
  return tokens;
}

// Every surface a token can legitimately sit on. Checking against all of them
// (rather than the one the designer had in mind) is the point: `--muted` cleared
// 4.5 on `--bg` and failed it on `--surface3`, and both are used.
const BACKDROPS = ["bg", "surface", "surface2", "surface3", "page-bg"];
// Text tokens must clear WCAG AA 4.5:1 for body-size text.
const TEXT_TOKENS = ["text", "muted", "quiet", "accent", "pass", "warn", "crit"];

const THEMES = [
  [":root[data-theme=\"light\"]", "light"],
  [":root[data-theme=\"dark\"]", "dark"],
] as const;

for (const [selector, label] of THEMES) {
  const tokens = tokensFrom(selector);

  test(`${label}: text tokens clear WCAG AA (4.5:1) on every backdrop`, () => {
    const failures: string[] = [];
    for (const token of TEXT_TOKENS) {
      for (const backdrop of BACKDROPS) {
        const ratio = contrast(tokens[token], tokens[backdrop]);
        if (ratio < 4.5) {
          failures.push(`--${token} on --${backdrop}: ${ratio.toFixed(2)}`);
        }
      }
    }
    expect(failures, failures.join("; ")).toEqual([]);
  });

  test(`${label}: interactive borders clear WCAG 1.4.11 (3:1)`, () => {
    // Form controls, buttons and radio/checkbox indicators use
    // --line-interactive. --line / --line-strong are decorative separators and
    // are deliberately NOT held to this bar; that is why there are two tokens.
    const failures: string[] = [];
    for (const backdrop of BACKDROPS) {
      const ratio = contrast(tokens["line-interactive"], tokens[backdrop]);
      if (ratio < 3) {
        failures.push(`--line-interactive on --${backdrop}: ${ratio.toFixed(2)}`);
      }
    }
    expect(failures, failures.join("; ")).toEqual([]);
  });

  test(`${label}: status text is legible on its own soft chip`, () => {
    for (const token of ["pass", "warn", "crit"] as const) {
      const ratio = contrast(tokens[token], tokens[`${token}-soft`]);
      expect(ratio, `--${token} on --${token}-soft is ${ratio.toFixed(2)}`)
        .toBeGreaterThanOrEqual(4.5);
    }
  });
}

test("the media-query dark block matches the explicit dark theme", () => {
  // Three places define the dark palette (prefers-color-scheme, [data-theme] and
  // the toggle). If they drift, the toggle silently shows a different — and
  // possibly unchecked — set of colours from the system default.
  const media = tokensFrom("@media (prefers-color-scheme: dark)");
  const explicit = tokensFrom(":root[data-theme=\"dark\"]");
  for (const [name, hex] of Object.entries(media)) {
    expect(explicit[name], `--${name} differs between the dark blocks`).toBe(hex);
  }
});

// ---------------------------------------------------------------------------
// R1-08 — test the colour that is PAINTED, not the token that names it.
//
// Every token above clears AA. The screen still had text below it, because two
// rules dimmed compliant tokens after the fact: a placeholder at
// `color-mix(… 74%, transparent)` (3.68:1 on a light card) and the skipped
// gate's label at `opacity: 0.62` (2.7:1). Both are invisible to a test that
// stops at the token, which is why both survived a contrast pass.
// ---------------------------------------------------------------------------

const APP_CSS = readFileSync(join(process.cwd(), "src/styles/app.css"), "utf8");

/** Composite `fg` at `alpha` over `bg` — what the compositor actually shows. */
function composite(fg: string, bg: string, alpha: number): string {
  const f = fg.replace("#", "");
  const b = bg.replace("#", "");
  const channel = (i: number) =>
    Math.round(
      parseInt(f.slice(i, i + 2), 16) * alpha +
        parseInt(b.slice(i, i + 2), 16) * (1 - alpha),
    );
  return (
    "#" +
    [0, 2, 4]
      .map((i) => channel(i).toString(16).padStart(2, "0"))
      .join("")
  );
}

/** The first declaration matching `selector { … prop: value }`, or undefined.
 *
 * Located by index rather than by a regex built from the selector: these
 * selectors are full of regex metacharacters (`.`, `[`, `]`, `=`, `"`), and a
 * mis-escaped pattern silently matches nothing — which, in a test that asserts
 * "this rule is absent", reads as a pass.
 */
function declaration(selector: string, prop: string): string | undefined {
  const start = APP_CSS.indexOf(selector);
  if (start === -1) return undefined;
  const open = APP_CSS.indexOf("{", start);
  const body = APP_CSS.slice(open + 1, APP_CSS.indexOf("}", open));
  for (const line of body.split(";")) {
    const [name, ...rest] = line.split(":");
    if (name.trim() === prop) return rest.join(":").trim();
  }
  return undefined;
}

test("composite() agrees with a hand-computed blend", () => {
  // Sanity: the helper is the thing every assertion below leans on.
  expect(composite("#000000", "#ffffff", 0.5)).toBe("#808080");
  expect(composite("#ffffff", "#000000", 1)).toBe("#ffffff");
  expect(composite("#123456", "#abcdef", 0)).toBe("#abcdef");
});

for (const [selector, label] of THEMES) {
  const tokens = tokensFrom(selector);

  test(`${label}: the prompt placeholder is readable as painted`, () => {
    // Regression guard on the RULE as well as the ratio: reverting to an alpha
    // blend of --muted is how this dropped to 3.68:1 the first time.
    const declared = declaration(".promptbar textarea::placeholder", "color");
    expect(declared, "placeholder colour rule went missing").toBeDefined();
    expect(
      declared,
      "placeholder must not be an alpha-thinned token — composite it and it fails AA",
    ).not.toMatch(/transparent/);

    const painted = declared!.replace(/var\(--([\w-]+)\)/, (_, name) => tokens[name]);
    for (const backdrop of BACKDROPS) {
      expect(
        contrast(painted, tokens[backdrop]),
        `${label}: placeholder on --${backdrop}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  test(`${label}: a skipped gate's own text still reaches AA`, () => {
    // The label and status of a skipped gate say WHICH check did not run. That
    // is the sentence the user has to read in order to act.
    const dimmed = declaration('.gate[data-status="skipped"] .gt', "opacity");
    expect(
      dimmed,
      "text must not be dimmed by opacity; use a token that passes at full strength",
    ).toBeUndefined();

    for (const backdrop of BACKDROPS) {
      expect(
        contrast(tokens.quiet, tokens[backdrop]),
        `${label}: skipped gate text on --${backdrop}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  test(`${label}: the skipped gate's icon chip still clears non-text 3:1`, () => {
    // The chip is decoration beside a text label that says the same thing, so
    // 3:1 is its bar — but it IS dimmed, so the bar is checked after dimming.
    const opacity = Number(
      declaration('.gate[data-status="skipped"] .gi', "opacity") ?? "1",
    );
    expect(opacity).toBeLessThan(1);
    const painted = composite(tokens.muted, tokens.bg, opacity);
    expect(
      contrast(painted, tokens.bg),
      `${label}: dimmed gate icon on --bg`,
    ).toBeGreaterThanOrEqual(3);
  });
}
