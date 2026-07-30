# ODForge final quality audit

Date: 2026-07-16  
Scope: Python core/CLI/MCP/Web API, React cockpit, documentation, dependency
manifests, tests and production build.

## Outcome

All ten findings in the Web quality baseline are resolved. No P0–P3 item from
that baseline remains open.

| Dimension | Baseline | Final | Evidence |
|---|---:|---:|---|
| Accessibility | 2/4 | 4/4 | Regeneration field named; wait clock removed from live announcements; native buttons and plain-language gate labels; 44 px coarse-pointer targets. |
| Performance | 3/4 | 4/4 | Preview thumbnails lazy-load and decode asynchronously; production JS remains about 56.1 kB gzip. |
| Responsive design | 2/4 | 4/4 | Prompt/header reflow at 840/600/420 px, single-column phone grid, scrollable tabs, 16 px mobile inputs and stacked regeneration form. |
| Theming | 4/4 | 4/4 | Semantic light/dark tokens and reduced-motion behavior preserved. |
| Anti-patterns | 2/4 | 4/4 | Both heavy side stripes removed; native interaction semantics replace custom keyboard emulation. |
| **Total** | **13/20** | **20/20** | **Flagship quality bar met by source inspection and automated verification.** |

## Broader engineering improvements

- Added bounded ZIP/XML parsing (members, total bytes, member bytes, markup
  complexity) across validation, check and template extraction.
- Prevented rejected archives from reaching LibreOffice.
- Added strict Web input enums/lengths, generation and regeneration limits,
  per-job mutation locks, cancellation, approval timeout, retention and artifact
  cleanup.
- Made remote binding explicit with `--allow-remote`; unsafe CORS overrides now
  fail closed.
- Raised support to Python 3.11+ so `odfdo` can coexist with patched
  `lxml>=6.1,<7`; upgraded Vite/Vitest and regenerated the npm lockfile.
- Added Ruff as a declared development dependency and cleared the entire
  Python source/test tree.
- Added `.env.example`, updated version/test badges and corrected stale CORS,
  CLI and test-count documentation.

## Final verification

- Python 3.12: **402 passed**.
- Ruff: **all checks passed**.
- React/Vitest: **150 passed** across 18 files.
- TypeScript + Vite production build: **passed**; JS 170.26 kB / 56.11 kB gzip,
  CSS 26.62 kB / 5.69 kB gzip.
- `npm audit --audit-level=moderate`: **0 vulnerabilities**.
- `pip-audit --local`: **no known vulnerabilities** (the editable local
  `odforge` package is correctly skipped because it is not a PyPI release).
- Security findings schema: **PASS, 0 current confirmed findings**.

## Operational follow-up

The ignored local `odforge/.env` was never tracked, but its DeepSeek key should
be rotated because it became visible to the local audit tool transcript. No
secret value is reproduced in source, logs or reports.
