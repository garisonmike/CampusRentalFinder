/**
 * The one place an API error payload is parsed.
 *
 * The backend returns exactly one shape from every endpoint
 * (`backend/config/api/errors.py`):
 *
 *     { error: { code, message, field_errors, request_id } }
 *
 * `message` is always a plain string, `field_errors` is always present and may
 * be empty. That invariant is asserted on the backend across thirteen
 * exception kinds, so this parser does not need to guess — and no page may
 * parse a payload itself. `eslint.config.js` enforces that with a
 * `no-restricted-syntax` rule, because the failure mode is not a crash: a page
 * that reads `response.data.detail` gets `undefined`, renders an empty error
 * box, and looks like it works.
 */

/** Every code the API can return. Adding one here is a compile error at each
 *  exhaustive switch, which is the point. */
export const API_ERROR_CODES = [
  "validation_failed",
  "not_authenticated",
  "permission_denied",
  "not_found",
  "method_not_allowed",
  "throttled",
  "conflict",
  "server_error",
  "verification_required",
  "rate_limited",
  "not_reviewable",
  "review_frozen",
  "document_rejected",
  "erasure_blocked",
] as const;

export type ApiErrorCode = (typeof API_ERROR_CODES)[number];

/** Anything that is not a code we know about. Kept distinct from
 *  `server_error` so a deployed frontend meeting a newer backend degrades
 *  visibly rather than silently mislabelling. */
export const UNKNOWN_CODE = "unknown" as const;

export type ErrorCode = ApiErrorCode | typeof UNKNOWN_CODE;

export interface ApiError {
  readonly code: ErrorCode;
  /** One human-readable sentence. Never a list, never a dict. */
  readonly message: string;
  /** Per-field messages, always present, possibly empty. */
  readonly fieldErrors: Readonly<Record<string, readonly string[]>>;
  /** Paste this into a support ticket and the exact log lines are findable. */
  readonly requestId: string;
  readonly status: number;
  /** True when the request never reached the server at all. */
  readonly isNetworkError: boolean;
}

function isKnownCode(value: unknown): value is ApiErrorCode {
  return typeof value === "string" && (API_ERROR_CODES as readonly string[]).includes(value);
}

function readFieldErrors(value: unknown): Record<string, readonly string[]> {
  if (!value || typeof value !== "object") return {};

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([field, messages]) => [
      field,
      (Array.isArray(messages) ? messages : [messages]).map(String),
    ]),
  );
}

const NETWORK_MESSAGE =
  "Could not reach the server. Check your connection and try again.";

/**
 * Turn anything a request threw into an `ApiError`.
 *
 * Total by construction: a network failure, an HTML error page from a proxy,
 * and a malformed body all produce a valid `ApiError` rather than throwing
 * again inside the error handler — which is the worst place to throw, because
 * it replaces a message the user could act on with a blank screen.
 */
export function toApiError(thrown: unknown): ApiError {
  const response = (thrown as { response?: { status?: number; data?: unknown } })?.response;

  if (!response) {
    return {
      code: UNKNOWN_CODE,
      message: NETWORK_MESSAGE,
      fieldErrors: {},
      requestId: "",
      status: 0,
      isNetworkError: true,
    };
  }

  const status = response.status ?? 0;
  const body = (response.data as { error?: Record<string, unknown> } | undefined)?.error;

  if (!body) {
    // A proxy's HTML 502, a gateway timeout, or anything else that never
    // reached our exception handler. Not a contract violation on our side, so
    // it is reported as unknown rather than blamed on a code.
    return {
      code: UNKNOWN_CODE,
      message: "Something went wrong. Please try again.",
      fieldErrors: {},
      requestId: "",
      status,
      isNetworkError: false,
    };
  }

  return {
    code: isKnownCode(body.code) ? body.code : UNKNOWN_CODE,
    message:
      typeof body.message === "string" && body.message
        ? body.message
        : "Something went wrong. Please try again.",
    fieldErrors: readFieldErrors(body.field_errors),
    requestId: typeof body.request_id === "string" ? body.request_id : "",
    status,
    isNetworkError: false,
  };
}

/**
 * Codes worth retrying automatically.
 *
 * Deliberately short. A 409 is not retryable — the resource is in the wrong
 * state and it will still be in the wrong state a second later, so retrying
 * turns one clear conflict into three. A 400 is not retryable for the same
 * reason with the payload.
 */
export function isRetryable(error: ApiError): boolean {
  if (error.isNetworkError) return true;
  if (error.code === "server_error") return true;
  // A throttle IS retryable, but only after its window. The query layer
  // supplies the delay; retrying immediately would extend the ban.
  if (error.code === "throttled" || error.code === "rate_limited") return true;
  return false;
}

/**
 * What the user is told, in one place.
 *
 * The backend's `message` is written for a person and is usually the right
 * thing to show. These overrides exist where the API cannot know the context:
 * it does not know whether the caller is a browser that can offer a login
 * link, and "Not found." is unhelpful next to a page that already says what
 * the user was looking for.
 */
export function userFacingMessage(error: ApiError): string {
  switch (error.code) {
    case "not_authenticated":
      return "Please sign in to continue.";
    case "permission_denied":
      return error.message || "You do not have permission to do that.";
    case "not_found":
      return "We could not find that.";
    case "throttled":
    case "rate_limited":
      return error.message || "You are doing that too often. Please wait a moment.";
    case "server_error":
      return "Something went wrong on our side. We have been told.";
    case "validation_failed":
    case "conflict":
    case "verification_required":
    case "not_reviewable":
    case "review_frozen":
    case "document_rejected":
    case "erasure_blocked":
    case "method_not_allowed":
      // The backend wrote these for a person and knows the domain reason.
      return error.message;
    case UNKNOWN_CODE:
      return error.message;
    default: {
      // Exhaustiveness. A new code in API_ERROR_CODES that nobody handled is a
      // COMPILE error here rather than a silent fallthrough to a generic
      // message -- which is exactly how a frontend ends up showing "Something
      // went wrong" for a case the backend explained carefully.
      const exhaustive: never = error.code;
      return exhaustive;
    }
  }
}

/** Whether this error should send the user to sign in. */
export function requiresSignIn(error: ApiError): boolean {
  return error.code === "not_authenticated";
}

/** The first message for one field, for inline form display. */
export function fieldError(error: ApiError, field: string): string | undefined {
  return error.fieldErrors[field]?.[0];
}

/** Errors belonging to no field, which a form renders in one predictable
 *  place rather than dropping. */
export function nonFieldErrors(error: ApiError): readonly string[] {
  return error.fieldErrors.non_field_errors ?? [];
}
