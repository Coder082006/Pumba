/**
 * Where the access token lives in a browser — ADR 0008, SRS §30.4.
 *
 *   "The web portals use bearer tokens held in memory with refresh tokens in
 *    HttpOnly, Secure, SameSite=Strict cookies."
 *
 * Two rules this module exists to enforce, neither of which the browser can
 * enforce for us:
 *
 *   1. **The access token is held in memory only.** Not localStorage, not
 *      sessionStorage, not a cookie this code can read. Anything script-
 *      readable and persistent survives the tab and is exfiltrable by a
 *      single XSS; a variable in a module closure dies with the page.
 *
 *   2. **The refresh token is never stored at all.** The login response
 *      carries it because the API is client-agnostic and Flutter needs it in
 *      the body — but in a browser it is the cookie the server set that does
 *      the work. Reading it here would put a 30-day credential somewhere
 *      script can reach, which is exactly what the cookie exists to prevent.
 *
 * `discardRefreshToken` is a named no-op rather than an absence, so the
 * decision is visible at the call site instead of looking like an oversight.
 */

export interface Principal {
  publicId: string;
  roles: string[];
}

let accessToken: string | null = null;
let expiresAt: number | null = null;
let principal: Principal | null = null;

/** Record a freshly issued access token. Never persists it. */
export function setAccessToken(token: string, expiresInSeconds: number): void {
  accessToken = token;
  expiresAt = Date.now() + expiresInSeconds * 1000;
}

export function getAccessToken(): string | null {
  if (accessToken !== null && expiresAt !== null && Date.now() >= expiresAt) {
    // Expired tokens are dropped rather than sent: a request that is going to
    // 401 anyway should not carry a credential across the network.
    clearSession();
  }
  return accessToken;
}

export function setPrincipal(next: Principal | null): void {
  principal = next;
}

export function getPrincipal(): Principal | null {
  return principal;
}

/**
 * Deliberately discards the refresh token from a login response.
 *
 * The server has already set the HttpOnly cookie for this origin (ADR 0008);
 * the body copy exists for the mobile clients. Keeping it here would defeat
 * the cookie.
 */
export function discardRefreshToken(_refreshToken: string): void {
  /* intentionally empty — see the module docstring */
}

export function clearSession(): void {
  accessToken = null;
  expiresAt = null;
  principal = null;
}

/** Authorization header for an authenticated request, or nothing. */
export function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token === null ? {} : { Authorization: `Bearer ${token}` };
}
