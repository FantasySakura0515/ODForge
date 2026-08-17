import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// CI runners are several times slower than a dev machine: the same 26 files take
// ~5s locally and ~42s on the node 20 lane. Testing Library's 1000ms default for
// findBy*/waitFor turns that gap into flaky failures — a render still in flight
// is reported as "unable to find element", which is noise, not signal (it landed
// as two failures on node 20.19 while node 22/24 passed the identical code).
// 5s still fails fast on a genuinely missing element; vitest.config.ts lifts the
// per-test cap so a chain of these waits cannot hit the test timeout first.
configure({ asyncUtilTimeout: 5000 });
