import axios, {
  type AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  REFRESH_COOKIE_MODE,
  clearTokens,
  getAccessToken,
  getRefreshToken,
  hasRefreshCredential,
  setTokens,
} from "./tokens";
import type { Paginated } from "./types";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "/api/v1";

/** Called when refreshing fails and the session is unrecoverable. */
type SessionExpiredHandler = () => void;

let onSessionExpired: SessionExpiredHandler = () => {};

export function setSessionExpiredHandler(handler: SessionExpiredHandler): void {
  onSessionExpired = handler;
}

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retried?: boolean;
  /** Opt a request out of the refresh interceptor (the refresh call itself). */
  _skipAuthRefresh?: boolean;
}

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: { "Content-Type": "application/json" },
  // Needed for the httpOnly refresh cookie once the backend sets one.
  withCredentials: REFRESH_COOKIE_MODE,
});

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ---------------------------------------------------------------------------
// Single-flight refresh
//
// The previous client fired one refresh per concurrent 401. With
// ROTATE_REFRESH_TOKENS on, the first rotation invalidates every other in-flight
// refresh token, so N-1 of them fail and the user is logged out mid-session.
//
// Here, the first 401 starts a refresh and every subsequent 401 awaits the same
// promise. One network call, one rotation, all requests replayed.
// ---------------------------------------------------------------------------

let refreshPromise: Promise<string> | null = null;

async function performRefresh(): Promise<string> {
  const refresh = getRefreshToken();
  const body = REFRESH_COOKIE_MODE ? {} : { refresh };

  const response = await axios.post<{ access: string; refresh?: string }>(
    `${API_BASE_URL}/auth/token/refresh/`,
    body,
    {
      headers: { "Content-Type": "application/json" },
      withCredentials: REFRESH_COOKIE_MODE,
    },
  );

  setTokens({ access: response.data.access, refresh: response.data.refresh ?? undefined });
  return response.data.access;
}

/**
 * Refresh the access token, coalescing concurrent callers onto one request.
 * Exported so the auth store and the tests can await the same promise.
 */
export function refreshAccessToken(): Promise<string> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;

    const isAuthFailure = error.response?.status === 401;
    const canRetry = config && !config._retried && !config._skipAuthRefresh;

    if (!isAuthFailure || !canRetry || !hasRefreshCredential()) {
      return Promise.reject(error);
    }

    config._retried = true;

    try {
      const access = await refreshAccessToken();
      config.headers.Authorization = `Bearer ${access}`;
      return await api.request(config);
    } catch (refreshError) {
      clearTokens();
      onSessionExpired();
      return Promise.reject(refreshError);
    }
  },
);

// ---------------------------------------------------------------------------
// Typed helpers
// ---------------------------------------------------------------------------

export async function get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  const { data } = await api.get<T>(url, config);
  return data;
}

export async function post<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  const { data } = await api.post<T>(url, body, config);
  return data;
}

export async function patch<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  const { data } = await api.patch<T>(url, body, config);
  return data;
}

export async function del(url: string, config?: AxiosRequestConfig): Promise<void> {
  await api.delete(url, config);
}

/**
 * Fetch one page of a paginated list endpoint.
 *
 * Always returns the envelope. Callers read `.results`; nothing in this
 * codebase may treat a list response as an array.
 */
export async function getPage<T>(
  url: string,
  params?: Record<string, unknown>,
): Promise<Paginated<T>> {
  return get<Paginated<T>>(url, { params });
}

/** An empty page, for rendering before the first fetch resolves. */
export function emptyPage<T>(): Paginated<T> {
  return { count: 0, next: null, previous: null, results: [] };
}

export default api;
