import type { components, paths } from "./schema";

/**
 * Thin aliases over the generated schema.
 *
 * Nothing in this file describes a shape by hand. If a name here stops
 * resolving, the backend contract changed — regenerate with
 * `npm run generate:types` and fix the call site, do not patch the type.
 */

export type Schemas = components["schemas"];
export type Paths = paths;

/** Response body of a GET on `P`, status 200. */
export type GetResponse<P extends keyof Paths> = Paths[P] extends {
  get: { responses: { 200: { content: { "application/json": infer R } } } };
}
  ? R
  : never;

/** Request body of a POST on `P`. */
export type PostBody<P extends keyof Paths> = Paths[P] extends {
  post: { requestBody?: { content: { "application/json": infer B } } };
}
  ? B
  : never;

/**
 * The pagination envelope (`config/api/pagination.py`).
 *
 * The previous client typed every list endpoint as a bare array and called
 * `.map()` on it, which is why no list view ever rendered.
 *
 * Then this type was written by hand and **drifted**: it described
 * `{count, next, previous, results}` while the API had added `page`,
 * `page_size` and `total_pages`. Nothing caught it, because a hand-written
 * type cannot disagree with itself -- exactly the "declared in two places"
 * shape `docs/OPERATIONS.md` warns about, with the schema as the copy that is
 * actually true.
 *
 * So the envelope is now derived from a real generated response and the
 * element type is substituted in. If the backend changes the envelope, this
 * stops compiling.
 */
type GeneratedEnvelope = GetResponse<"/api/v1/properties/">;

export type Paginated<T> = Omit<GeneratedEnvelope, "results"> & { results: T[] };

export interface PageParams {
  page?: number;
  page_size?: number;
  /** Only the property list accepts this, and only these values. */
  ordering?: "distance" | "rent" | "-published_at";
}

export interface AuthTokens {
  access: string;
  refresh: string;
}
