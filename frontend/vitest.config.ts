/// <reference types="vitest" />
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { defineConfig } from "vite";

// Kept separate from vite.config.ts so the dev and build config stays free of
// test-only settings.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // The client falls back to a relative "/api/v1" base, which jsdom resolves
    // against localhost and MSW cannot match. Pin an absolute base for tests.
    env: { VITE_API_URL: "http://api.test/api/v1" },
    testTimeout: 15_000,
    hookTimeout: 15_000,
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/api/**", "src/app/**", "src/stores/**", "src/theme/**", "src/lib/**"],
      exclude: ["src/api/schema.d.ts", "src/components/ui/**"],
    },
  },
});
