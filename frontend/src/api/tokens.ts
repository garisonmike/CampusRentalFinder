/**
 * Token storage.
 *
 * The access token lives in memory only. It never touches localStorage, so an
 * XSS payload cannot read it out of persistent storage after the fact, and it
 * disappears when the tab closes.
 *
 * The refresh token is the harder question. The intended design is an
 * httpOnly, Secure, SameSite=Lax cookie set by the backend, which script
 * cannot read at all. The backend does not do that yet — the current
 * /auth/login/ endpoint returns both tokens in the JSON body and sets no
 * cookie (see docs/AUDIT.md). Until that changes:
 *
 *   EXPOSURE: the refresh token is held in memory alongside the access token
 *   and is lost on reload, so a returning user must log in again. That is a
 *   deliberate trade: a 7-day credential in localStorage is readable by any
 *   injected script, and persistence is not worth that. Session continuity
 *   returns for free the moment the backend sets the cookie.
 *
 * When the backend does set an httpOnly refresh cookie, `refreshToken` here
 * becomes permanently null, `hasRefreshCredential()` starts reporting true
 * from a separate non-sensitive marker, and the refresh call relies on the
 * browser attaching the cookie. Nothing else in the client changes.
 */

let accessToken: string | null = null;
let refreshToken: string | null = null;

/** True when the backend sets the refresh token as an httpOnly cookie. */
export const REFRESH_COOKIE_MODE = false;

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  return refreshToken;
}

export function setTokens(tokens: { access: string; refresh?: string | null }): void {
  accessToken = tokens.access;
  if (tokens.refresh !== undefined) {
    refreshToken = tokens.refresh;
  }
}

export function clearTokens(): void {
  accessToken = null;
  refreshToken = null;
}

/** Whether a refresh is worth attempting at all. */
export function hasRefreshCredential(): boolean {
  return REFRESH_COOKIE_MODE || refreshToken !== null;
}
