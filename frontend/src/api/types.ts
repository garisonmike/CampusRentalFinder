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
 * DRF's PageNumberPagination envelope.
 *
 * The previous client typed every list endpoint as a bare array and called
 * .map() on it, which is why no list view ever rendered. Every paginated
 * response goes through this type.
 */
export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface PageParams {
  page?: number;
  page_size?: number;
  ordering?: string;
  search?: string;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}
