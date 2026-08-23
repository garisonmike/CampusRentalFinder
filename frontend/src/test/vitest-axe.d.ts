/**
 * vitest-axe declares its matchers on the legacy global `Vi` namespace, which
 * Vitest 3 no longer reads. Re-declare them on the `vitest` module so
 * `expect(...).toHaveNoViolations()` type-checks.
 */
import type { AxeMatchers } from "vitest-axe/matchers";

declare module "vitest" {
  interface Assertion extends AxeMatchers {}
  interface AsymmetricMatchersContaining extends AxeMatchers {}
}
