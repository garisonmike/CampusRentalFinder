import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, vi } from "vitest";
import * as matchers from "vitest-axe/matchers";
import { expect } from "vitest";

import { clearTokens } from "@/api/tokens";
import { useAuthStore } from "@/stores/auth";
import { clearTokens as clearThemeTokens } from "@/theme/tokens";

import { server } from "./msw/server";

expect.extend(matchers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());

beforeEach(() => {
  // jsdom implements neither, and both are called during render.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });

  if (!("ResizeObserver" in window)) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  clearTokens();
  clearThemeTokens();
  localStorage.clear();
  useAuthStore.setState({ user: null, status: "idle" });
  vi.clearAllMocks();
});
