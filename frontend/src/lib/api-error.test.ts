import { describe, expect, it } from "vitest";

import {
  API_ERROR_CODES,
  type ApiError,
  fieldError,
  isRetryable,
  nonFieldErrors,
  requiresSignIn,
  toApiError,
  userFacingMessage,
} from "./api-error";

/**
 * The error client.
 *
 * The failure this guards against is not a crash. A page that reads
 * `response.data.detail` against this API gets `undefined`, renders an empty
 * error box, and looks like it works — so the tests that matter most are the
 * ones asserting the parser is total and the switch is exhaustive.
 */

function thrown(status: number, body: unknown) {
  return { response: { status, data: body } };
}

function apiBody(overrides: Record<string, unknown> = {}) {
  return {
    error: {
      code: "validation_failed",
      message: "Must be between 1 and 5.",
      field_errors: { rating: ["Must be between 1 and 5."] },
      request_id: "abc123",
      ...overrides,
    },
  };
}

describe("toApiError", () => {
  it("reads the contract shape", () => {
    const error = toApiError(thrown(400, apiBody()));

    expect(error.code).toBe("validation_failed");
    expect(error.message).toBe("Must be between 1 and 5.");
    expect(error.fieldErrors).toEqual({ rating: ["Must be between 1 and 5."] });
    expect(error.requestId).toBe("abc123");
    expect(error.status).toBe(400);
  });

  it("is total: a network failure still produces an ApiError", () => {
    // Throwing inside the error handler is the worst place to throw -- it
    // replaces a message the user could act on with a blank screen.
    const error = toApiError(new Error("Network Error"));

    expect(error.isNetworkError).toBe(true);
    expect(error.message).toContain("connection");
    expect(error.fieldErrors).toEqual({});
  });

  it("is total: an HTML error page from a proxy does not throw", () => {
    const error = toApiError(thrown(502, "<html><body>Bad Gateway</body></html>"));

    expect(error.code).toBe("unknown");
    expect(error.status).toBe(502);
  });

  it("is total: a malformed body does not throw", () => {
    expect(() => toApiError(thrown(500, { error: null }))).not.toThrow();
    expect(() => toApiError(thrown(500, {}))).not.toThrow();
    expect(() => toApiError(undefined)).not.toThrow();
    expect(() => toApiError(null)).not.toThrow();
  });

  it("labels an unrecognised code as unknown rather than guessing", () => {
    // A deployed frontend meeting a newer backend degrades visibly instead of
    // silently mislabelling a case it has never seen.
    const error = toApiError(thrown(418, apiBody({ code: "brand_new_code" })));

    expect(error.code).toBe("unknown");
    expect(error.message).toBe("Must be between 1 and 5.");
  });

  it("keeps the request id, which is how support finds the logs", () => {
    expect(toApiError(thrown(400, apiBody())).requestId).toBe("abc123");
  });

  it("normalises a non-array field error to an array", () => {
    const error = toApiError(
      thrown(400, apiBody({ field_errors: { rating: "Just a string." } })),
    );

    expect(error.fieldErrors.rating).toEqual(["Just a string."]);
  });

  it("survives field_errors being absent", () => {
    const error = toApiError(thrown(403, apiBody({ field_errors: undefined })));

    expect(error.fieldErrors).toEqual({});
  });
});

describe("userFacingMessage", () => {
  function errorWith(code: ApiError["code"], message = "Backend sentence."): ApiError {
    return {
      code,
      message,
      fieldErrors: {},
      requestId: "r1",
      status: 400,
      isNetworkError: false,
    };
  }

  it.each(API_ERROR_CODES)("returns a non-empty string for %s", (code) => {
    // Exhaustiveness is checked by the compiler; this checks the runtime
    // result is renderable for every code, including ones added later.
    const message = userFacingMessage(errorWith(code));

    expect(typeof message).toBe("string");
    expect(message.length).toBeGreaterThan(0);
  });

  it("prefers the backend's sentence for domain refusals", () => {
    // The backend knows why. Replacing "A stay must reach 30 days" with a
    // generic message throws away the part that took the work.
    const message = userFacingMessage(
      errorWith("not_reviewable", "A stay must reach 30 days before it can be reviewed."),
    );

    expect(message).toContain("30 days");
  });

  it("overrides where the API cannot know the context", () => {
    // The API does not know it is talking to a browser that can offer a
    // login link.
    expect(userFacingMessage(errorWith("not_authenticated"))).toContain("sign in");
  });

  it("does not leak a server error's internals", () => {
    const message = userFacingMessage(
      errorWith("server_error", "IntegrityError at line 402 in tenancies/services.py"),
    );

    expect(message).not.toContain("services.py");
  });
});

describe("isRetryable", () => {
  function errorWith(code: ApiError["code"], isNetworkError = false): ApiError {
    return { code, message: "", fieldErrors: {}, requestId: "", status: 0, isNetworkError };
  }

  it("retries a network failure", () => {
    expect(isRetryable(errorWith("unknown", true))).toBe(true);
  });

  it("retries a server error", () => {
    expect(isRetryable(errorWith("server_error"))).toBe(true);
  });

  it("retries a throttle, which the query layer delays", () => {
    expect(isRetryable(errorWith("throttled"))).toBe(true);
    expect(isRetryable(errorWith("rate_limited"))).toBe(true);
  });

  it("does NOT retry a conflict", () => {
    // The resource is in the wrong state and will still be a second later.
    // Retrying turns one clear conflict into three.
    expect(isRetryable(errorWith("conflict"))).toBe(false);
    expect(isRetryable(errorWith("not_reviewable"))).toBe(false);
    expect(isRetryable(errorWith("review_frozen"))).toBe(false);
  });

  it("does NOT retry a validation failure", () => {
    expect(isRetryable(errorWith("validation_failed"))).toBe(false);
  });

  it("does NOT retry a permission denial", () => {
    // Retrying an authorisation failure is how a client generates its own
    // brute-force signature in somebody's alerting.
    expect(isRetryable(errorWith("permission_denied"))).toBe(false);
    expect(isRetryable(errorWith("not_authenticated"))).toBe(false);
  });
});

describe("form helpers", () => {
  const error = toApiError(
    thrown(
      400,
      apiBody({
        field_errors: {
          rating: ["Must be between 1 and 5.", "And an integer."],
          non_field_errors: ["Something was wrong."],
        },
      }),
    ),
  );

  it("gives the first message for a field", () => {
    expect(fieldError(error, "rating")).toBe("Must be between 1 and 5.");
  });

  it("returns undefined for a field with no error", () => {
    expect(fieldError(error, "comment")).toBeUndefined();
  });

  it("surfaces errors belonging to no field", () => {
    // A form has one predictable place to render these rather than dropping
    // them, which is what happens when a client only reads named fields.
    expect(nonFieldErrors(error)).toEqual(["Something was wrong."]);
  });

  it("returns an empty list when there are none", () => {
    expect(nonFieldErrors(toApiError(thrown(403, apiBody())))).toEqual([]);
  });
});

describe("requiresSignIn", () => {
  it("is true only for not_authenticated", () => {
    // "Log in" and "you may not do this" are different next steps, and a
    // client that conflates them shows a login box to someone already
    // signed in.
    const notAuthed = toApiError(thrown(401, apiBody({ code: "not_authenticated" })));
    const forbidden = toApiError(thrown(403, apiBody({ code: "permission_denied" })));

    expect(requiresSignIn(notAuthed)).toBe(true);
    expect(requiresSignIn(forbidden)).toBe(false);
  });
});
