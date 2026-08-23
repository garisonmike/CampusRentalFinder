import { isAxiosError } from "axios";

/**
 * Pull a human-readable message out of whatever a failed request threw.
 *
 * DRF is inconsistent about where the message lands: `detail` for permission
 * and auth failures, `message` for the hand-rolled views, and a
 * `{field: [errors]}` map for serializer validation. This flattens all three
 * so callers do not have to guess.
 */
export function getErrorMessage(error: unknown, fallback = "Something went wrong."): string {
  if (isAxiosError(error)) {
    const data = error.response?.data as
      | { detail?: string; message?: string; error?: string; [key: string]: unknown }
      | undefined;

    if (data) {
      if (typeof data.detail === "string") return data.detail;
      if (typeof data.message === "string") return data.message;
      if (typeof data.error === "string") return data.error;

      // Serializer errors: {"email": ["A user with that email already exists."]}
      const fieldMessages = Object.entries(data)
        .flatMap(([field, value]) => {
          const messages = Array.isArray(value) ? value : [value];
          return messages
            .filter((entry): entry is string => typeof entry === "string")
            .map((entry) => (field === "non_field_errors" ? entry : `${field}: ${entry}`));
        })
        .filter(Boolean);

      if (fieldMessages.length > 0) return fieldMessages.join("\n");
    }

    if (error.message) return error.message;
  }

  if (error instanceof Error && error.message) return error.message;

  return fallback;
}
