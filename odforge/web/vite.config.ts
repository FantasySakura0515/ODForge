import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Raised past vitest's 5s default because setup.ts lets a single waitFor
    // spend 5s on a slow runner; a test chaining two of them would otherwise trip
    // the per-test cap instead of reporting the assertion that actually failed.
    testTimeout: 20_000,
  },
});
