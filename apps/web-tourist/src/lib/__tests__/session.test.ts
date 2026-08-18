/**
 * Tests for the browser session store — ADR 0008, SRS §30.4.
 *
 * These assert properties the browser cannot enforce for us: that the access
 * token never reaches persistent storage, and that the refresh token is not
 * kept at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  authHeaders,
  clearSession,
  discardRefreshToken,
  getAccessToken,
  getPrincipal,
  setAccessToken,
  setPrincipal,
} from '../session';

describe('access token storage', () => {
  beforeEach(() => {
    clearSession();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    clearSession();
  });

  it('returns the token that was set', () => {
    setAccessToken('a-token', 900);
    expect(getAccessToken()).toBe('a-token');
  });

  it('never writes the token to localStorage', () => {
    // Anything script-readable and persistent survives the tab and is
    // exfiltrable by a single XSS.
    setAccessToken('a-token', 900);
    expect(localStorage.length).toBe(0);
    expect(JSON.stringify(localStorage)).not.toContain('a-token');
  });

  it('never writes the token to sessionStorage', () => {
    setAccessToken('a-token', 900);
    expect(sessionStorage.length).toBe(0);
  });

  it('never writes the token to document.cookie', () => {
    setAccessToken('a-token', 900);
    expect(document.cookie).not.toContain('a-token');
  });

  it('drops an expired token rather than sending it', () => {
    vi.useFakeTimers();
    setAccessToken('a-token', 60);
    vi.advanceTimersByTime(61_000);
    expect(getAccessToken()).toBeNull();
  });

  it('keeps a token that has not expired', () => {
    vi.useFakeTimers();
    setAccessToken('a-token', 60);
    vi.advanceTimersByTime(30_000);
    expect(getAccessToken()).toBe('a-token');
  });

  it('clears the principal when the token expires', () => {
    vi.useFakeTimers();
    setAccessToken('a-token', 60);
    setPrincipal({ publicId: 'p1', roles: ['TOURIST'] });
    vi.advanceTimersByTime(61_000);
    getAccessToken();
    expect(getPrincipal()).toBeNull();
  });
});

describe('refresh token handling', () => {
  beforeEach(() => {
    clearSession();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('stores the refresh token nowhere at all', () => {
    // The server has set the HttpOnly cookie for this origin; the body copy
    // exists for the mobile clients (ADR 0008). Keeping it here would put a
    // 30-day credential somewhere script can reach.
    discardRefreshToken('a-refresh-token');

    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.cookie).not.toContain('a-refresh-token');
    expect(getAccessToken()).toBeNull();
  });
});

describe('authHeaders', () => {
  beforeEach(clearSession);

  it('is empty when there is no session', () => {
    expect(authHeaders()).toEqual({});
  });

  it('carries a bearer token when there is one', () => {
    setAccessToken('a-token', 900);
    expect(authHeaders()).toEqual({ Authorization: 'Bearer a-token' });
  });
});

describe('clearSession', () => {
  it('removes both the token and the principal', () => {
    setAccessToken('a-token', 900);
    setPrincipal({ publicId: 'p1', roles: ['TOURIST'] });
    clearSession();
    expect(getAccessToken()).toBeNull();
    expect(getPrincipal()).toBeNull();
  });
});
