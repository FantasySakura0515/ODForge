import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  // Two pages: the existing cockpit (index.html) and the new editor (editor.html),
  // which is being built out to replace it.
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL("./index.html", import.meta.url)),
        editor: fileURLToPath(new URL("./editor.html", import.meta.url)),
      },
    },
  },
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
