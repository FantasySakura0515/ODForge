import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

/**
 * Layout guarantees that a unit test can actually hold.
 *
 * jsdom does not lay anything out, so "is the CTA above the fold at 504×692" is
 * not answerable here — that assertion belongs in a real browser and is not
 * faked. What *is* checkable, and what regressed before, is the rule set that
 * produces the behaviour: the sticky delivery bar, the phase-driven column
 * reorder, and the viewport-bounded desktop cockpit. If one of these rules is
 * deleted the layout silently reverts to "scroll past twelve thumbnails to find
 * the download button", and nothing else in the suite would notice.
 */

const CSS = readFileSync(join(process.cwd(), "src/styles/app.css"), "utf8");

/** The body of the first `@media <query> { ... }` block, brace-matched. */
function mediaBlock(query: string): string {
  const start = CSS.indexOf(`@media ${query}`);
  expect(start, `no @media ${query} block`).toBeGreaterThan(-1);
  let depth = 0;
  let i = CSS.indexOf("{", start);
  const open = i;
  for (; i < CSS.length; i++) {
    if (CSS[i] === "{") depth++;
    else if (CSS[i] === "}" && --depth === 0) break;
  }
  return CSS.slice(open + 1, i);
}

const NARROW = mediaBlock("(max-width: 920px)");
const WIDE = mediaBlock("(min-width: 921px)");

test("narrow viewports: the delivery bar sticks to the bottom of the screen", () => {
  // 12 pages of thumbnails is several screens tall on a 504×692 phone; the
  // download / 再鍛一份 actions must not live at the end of that scroll.
  expect(NARROW).toMatch(/\.foot\s*\{[^}]*position:\s*sticky/);
  expect(NARROW).toMatch(/\.foot\s*\{[^}]*bottom:\s*0/);
});

test("narrow viewports: the task header (再鍛一份 / 首頁) sticks to the top", () => {
  expect(NARROW).toMatch(/\.workhead\s*\{[^}]*position:\s*sticky/);
});

test("awaiting approval: the outline column is ordered BEFORE the thumbnail wall", () => {
  // grid-template-areas, not `order:` — the DOM order is unchanged, so the
  // keyboard and screen-reader order stays consistent with the visual one.
  const awaitRule = NARROW.match(
    /\.cockpit\[data-phase="await"\]\s*\{([^}]*)\}/,
  );
  expect(awaitRule, "no await-phase reorder rule").not.toBeNull();
  const areas = awaitRule![1].replace(/\s+/g, " ");
  expect(areas).toContain('"left" "center" "right"');
});

test("awaiting approval: the confirm CTA is sticky and the skeleton wall is collapsed", () => {
  expect(NARROW).toMatch(
    /\.cockpit\[data-phase="await"\]\s+\.confirmbar\s*\{[^}]*position:\s*sticky/,
  );
  // The wall has no content to show before approval; leaving it full height is
  // what pushed the CTA thousands of pixels down.
  expect(NARROW).toMatch(
    /\.cockpit\[data-phase="await"\]\s+\.grid\s*\{[^}]*max-height/,
  );
});

test("desktop: the cockpit is bounded by the viewport and each column scrolls", () => {
  expect(WIDE).toMatch(/\.workbench\s*\{[^}]*max-height:\s*100dvh/);
  expect(WIDE).toMatch(/\.rail,\s*\n?\s*\.center\s*\{[^}]*overflow-y:\s*auto/);
});

test("no rule reintroduces a whole-row opacity fade over a gate's reason text", () => {
  // The reason a gate did not run is the one actionable line in that row;
  // fading the row took it below AA.
  expect(CSS).not.toMatch(/\.gate\[data-status="skipped"\]\s*\{\s*opacity/);
});

// ---------------------------------------------------------------------------
// R2-06 — one order, not two.
//
// Only the `await` phase had visual order matching DOM order. Everywhere else
// the phone showed center → left → right over a DOM of left → center → right,
// so the column a sighted user saw first was several Tab stops away and a
// screen-reader user heard the three in a different sequence entirely.
// ---------------------------------------------------------------------------

/** The `grid-template-areas` value declared for `selector` inside `css`. */
function areasOf(css: string, selector: string): string {
  const start = css.indexOf(selector);
  expect(start, `no rule for ${selector}`).toBeGreaterThan(-1);
  const open = css.indexOf("{", start);
  const body = css.slice(open + 1, css.indexOf("}", open));
  const value = body.match(/grid-template-areas:\s*([^;]+)/)?.[1];
  expect(value, `${selector} declares no grid-template-areas`).toBeDefined();
  return value!.replace(/["\s]+/g, " ").trim();
}

test("narrow viewports: every phase lays the columns out in DOM order", () => {
  // DOM order is left → center → right (App.tsx renders the rails in that
  // order), so the visual order must be the same one.
  expect(areasOf(NARROW, ".cockpit,")).toBe("left center right");
  expect(areasOf(NARROW, '.cockpit[data-phase="await"]')).toBe("left center right");
});

test("narrow viewports: no order/flex-direction hack re-sequences the columns", () => {
  // `order` and `row-reverse` move pixels without moving the DOM — the exact
  // mismatch this rule set exists to avoid. grid-area is the only mechanism.
  expect(NARROW).not.toMatch(/\.rail\.(left|right)\s*\{[^}]*order:/);
  expect(NARROW).not.toMatch(/\.cockpit\s*\{[^}]*flex-direction:\s*\w+-reverse/);
});

test("coarse pointers get 44x44 targets in BOTH dimensions", () => {
  const COARSE = mediaBlock("(pointer: coarse)");
  const buttons = COARSE.match(/button[^{]*\{([^}]*)\}/)?.[1] ?? "";
  expect(buttons).toMatch(/min-height:\s*44px/);
  // The half that was missing: a 44px-tall, 20px-wide icon button is still
  // unhittable, and "touch is handled" is what stopped anyone re-checking.
  expect(buttons).toMatch(/min-width:\s*44px/);
});
