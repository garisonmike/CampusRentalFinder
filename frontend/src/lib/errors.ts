/**
 * Error helpers for call sites that only want a sentence.
 *
 * This file used to guess between DRF's `detail`, a hand-rolled `message`, and
 * a `{field: [errors]}` map, because the draft API returned all three. It does
 * not any more: every endpoint returns one shape
 * (`backend/config/api/errors.py`), so guessing is not just unnecessary but
 * actively wrong -- the old version returned `undefined` for the real shape
 * and rendered an empty error box.
 *
 * Parsing lives in `api-error.ts`. This is a convenience wrapper over it.
 */

import { toApiError, userFacingMessage } from "./api-error";

/**
 * A sentence to show the user, from anything a request threw.
 *
 * `fallback` is honoured only for a genuinely empty message; the backend
 * writes these for a person and usually knows the domain reason better than
 * the call site does.
 */
export function getErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  const parsed = toApiError(error);
  return userFacingMessage(parsed) || fallback;
}

export { toApiError, userFacingMessage } from "./api-error";
export type { ApiError, ApiErrorCode, ErrorCode } from "./api-error";
