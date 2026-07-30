# ODForge Web Technical Quality Baseline

Date: 2026-07-16  
Scope: `odforge/web` implementation, production build, and its Web API-facing states.  
Evidence: 146 Vitest tests, 375 Python tests collected, production Vite build, source inspection, and dependency audits.

## Anti-Patterns Verdict

**Fail (targeted, not systemic).** The cockpit has a distinctive document-forging identity and does not resemble a generic AI landing page, but two absolute-banned side-stripe treatments remain in QA findings and the error panel (`app.css:882`, `app.css:923`). Rounded surfaces are used frequently but mostly reflect a coherent control-room hierarchy rather than an interchangeable card grid. There is no gradient text, hero-metric template, neon purple/cyan marketing palette, or decorative glass-card system.

## Audit Health Score

| # | Dimension | Score | Key finding |
|---|---|---:|---|
| 1 | Accessibility | 2/4 | Regeneration input lacks an accessible label; the one-second waiting clock lives inside a polite live region; many touch targets are below 44 px. |
| 2 | Performance | 3/4 | Small bundle and transform/opacity motion are good, but every generated page preview loads eagerly. |
| 3 | Responsive Design | 2/4 | The shell collapses at 840 px, but the top prompt controls do not reflow for narrow phones and can overflow. |
| 4 | Theming | 4/4 | Semantic tokens cover both themes and explicit overrides; contrast repairs and reduced-motion handling are present. |
| 5 | Anti-Patterns | 2/4 | Two side-stripe patterns violate the project's adopted Impeccable rules. |
| **Total** |  | **13/20** | **Acceptable — significant targeted work needed** |

## Executive Summary

- Audit Health Score: **13/20 (Acceptable)**.
- Issues: **P0 0 / P1 4 / P2 4 / P3 2**.
- Highest priorities are phone-width layout, touch targets, the unlabeled regeneration field, and assistive-technology noise from the waiting clock.
- The production bundle is already lean (169.8 kB JS and 25.6 kB CSS; gzip 56.3/5.3 kB), all current automated tests pass, and reduced-motion support is unusually complete.

## Detailed Findings

### P1 Major

#### [P1] Narrow-phone top bar can overflow

- **Location:** `odforge/web/src/styles/app.css:86-259`, responsive rules at `app.css:1141-1150`.
- **Category:** Responsive.
- **Impact:** At common 320–430 px widths, brand, theme toggle, three document tabs, textarea, and submit button compete in one flex row. Users can lose controls off-screen or be forced into horizontal scrolling.
- **Standard:** WCAG 1.4.10 Reflow.
- **Recommendation:** Add explicit narrow-width composition: wrap the top bar, give the prompt bar a full row, and let document tabs/input/actions reflow without removing functionality.
- **Suggested command:** `/adapt`.

#### [P1] Touch targets are consistently below 44 × 44 px

- **Location:** theme buttons (`app.css:145-165`), document tabs (`app.css:190-212`), chips and advanced toggle (`app.css:267-303`), delete/confirm controls (`app.css:365-419`), dialog close/navigation (`app.css:680-723`), waiting cancel (`app.css:1126-1138`).
- **Category:** Accessibility / Responsive.
- **Impact:** Users on phones, touch laptops, or with motor impairments can miss small targets, especially in the dense prompt controls.
- **Standard:** WCAG 2.5.8 (minimum 24 px at AA); project target is the more robust 44 px convention.
- **Recommendation:** Preserve compact desktop density but enforce 44 px minimum hit areas for coarse pointers and make primary dialog controls 44 px in every context.
- **Suggested command:** `/adapt`.

#### [P1] Regeneration instruction field has no accessible name

- **Location:** `odforge/web/src/components/UnitDetail.tsx:138-146`.
- **Category:** Accessibility.
- **Impact:** Screen-reader users hear an unlabeled text field; placeholder text is not a durable or reliable label.
- **Standard:** WCAG 1.3.1, 3.3.2, 4.1.2.
- **Recommendation:** Add a visible or screen-reader label tied with `htmlFor`/`id`; retain the example as supporting text or placeholder.
- **Suggested command:** `/harden`.

#### [P1] Waiting timer can create repeated live-region announcements

- **Location:** `odforge/web/src/components/WaitingCard.tsx:29-45`.
- **Category:** Accessibility.
- **Impact:** The `role="status" aria-live="polite"` container contains a value that changes every second. Some assistive technologies may repeatedly announce elapsed time and interrupt navigation during a 30–60 second wait.
- **Standard:** WCAG 4.1.3 Status Messages; usability impact for screen-reader users.
- **Recommendation:** Limit the live region to meaningful stage changes and exclude the visual timer from live updates.
- **Suggested command:** `/harden`.

### P2 Minor

#### [P2] Preview images load eagerly

- **Location:** `odforge/web/src/components/UnitCell.tsx:23-26`.
- **Category:** Performance.
- **Impact:** A 30-page deck can trigger all 150-DPI PNG decodes at once, increasing memory and main-thread decode work even when most thumbnails are below the fold.
- **Recommendation:** Add `loading="lazy"` and `decoding="async"`; keep the selected detail image eager.
- **Suggested command:** `/optimize`.

#### [P2] Custom `div`/`li` buttons duplicate native interaction semantics

- **Location:** `UnitCell.tsx:10-20`, `GateRail.tsx:16-29`.
- **Category:** Accessibility / Anti-pattern.
- **Impact:** Hand-maintained Enter/Space behavior is more fragile than native button behavior and increases future regression risk.
- **Recommendation:** Prefer native buttons where DOM structure permits; otherwise centralize and test the custom interaction contract.
- **Suggested command:** `/harden`.

#### [P2] Page-count validation accepts fractional numbers client-side

- **Location:** `odforge/web/src/state/api.ts:33-35`.
- **Category:** Form robustness.
- **Impact:** Values such as `3.5` pass the client helper but are rejected by the integer API field, producing an avoidable server error instead of an inline message.
- **Recommendation:** Require `Number.isInteger(pages)` and add boundary tests.
- **Suggested command:** `/harden`.

#### [P2] Theme/status symbols do not expose plain-language state consistently

- **Location:** `GateRail.tsx:51-61`.
- **Category:** Accessibility.
- **Impact:** Visual symbols and color show pass/fail/active clearly, but assistive technology may announce only punctuation such as `✓`, `✕`, or `⟳` rather than the state name.
- **Recommendation:** Add screen-reader status text or an `aria-label` per gate while keeping decorative symbols hidden.
- **Suggested command:** `/clarify`.

### P3 Polish

#### [P3] QA findings use a banned side stripe

- **Location:** `odforge/web/src/styles/app.css:875-885`.
- **Category:** Anti-pattern.
- **Impact:** No functional harm, but it is a recognizable generic admin/AI treatment and conflicts with the adopted visual rules.
- **Recommendation:** Use a full severity-tinted border and the existing severity badge.
- **Suggested command:** `/polish`.

#### [P3] Error panel uses a banned side stripe

- **Location:** `odforge/web/src/styles/app.css:913-925`.
- **Category:** Anti-pattern.
- **Impact:** The error state looks like a generic callout rather than part of the cockpit's existing gate language.
- **Recommendation:** Use a full critical border or a compact leading error seal without an accent stripe.
- **Suggested command:** `/polish`.

## Patterns and Systemic Issues

- Compact controls were designed for a desktop competition-demo viewport; the 44 px target is missed across multiple component families rather than in one isolated control.
- Mobile adaptation currently changes the outer grid but not the internal information architecture of the top prompt area.
- Accessibility tests cover many names and focus paths, but automated assertions do not yet cover live-region churn, target sizing, or phone reflow.

## Positive Findings

- Both themes are built from semantic tokens, with explicit light/dark overrides and no scattered component color palette.
- `prefers-reduced-motion` disables every identified spinner, shimmer, entrance, and transition that could cause vestibular discomfort.
- Dialog focus entry, restoration, trapping, Escape, and arrow navigation are implemented and tested.
- Error states preserve user input and offer recovery; disabled features are honestly labeled rather than returning the wrong file type.
- The bundle is small, TypeScript is strict, and the front end has 146 passing tests across state, API, SSE, focus, and major flows.
- The control-room concept, visible validation gates, and document-first typography create a recognizable product identity.

## Recommended Actions

1. **[P1] `/adapt`** — Recompose the top prompt controls at phone widths and enforce coarse-pointer target sizes.
2. **[P1] `/harden`** — Repair form labels, live-region scope, integer validation, and state semantics.
3. **[P2] `/optimize`** — Lazy-load and asynchronously decode preview thumbnails.
4. **[P2] `/clarify`** — Expose gate outcomes in plain language to assistive technologies.
5. **[P3] `/polish`** — Remove the two side-stripe patterns and perform the final consistency pass.

Re-run `/audit` after fixes to measure the new score.
