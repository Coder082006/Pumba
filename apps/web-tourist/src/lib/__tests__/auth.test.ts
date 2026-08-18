/**
 * Tests for the authentication client — SRS §24.3, §24.4, TC-013.
 *
 * The non-enumeration tests matter most: the server returns identical bodies
 * for an unknown address and a wrong password, and a screen that said "no
 * account with that email" would undo that at the last step.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../api';
import { fieldErrorsFrom, login, loginBannerFor, requiresMfa } from '../auth';
import { clearSession, getAccessToken, getPrincipal } from '../session';

function errorOf(code: string, message = 'x', details: unknown[] = [], status = 401) {
  return new ApiRequestError(status, {
    code,
    message,
    details,
    request_id: null,
    retryable: false,
  } as never);
}

describe('login', () => {
  beforeEach(() => {
    clearSession();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearSession();
  });

  it('stores the access token and principal, and keeps the refresh token nowhere', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        data: {
          access_token: 'access-1',
          refresh_token: 'refresh-1',
          token_type: 'Bearer',
          expires_in: 900,
          principal: { public_id: 'p1', roles: ['TOURIST'] },
        },
      }),
      headers: new Headers(),
    });
    vi.stubGlobal('fetch', fetchMock);

    await login({ email: 'alice@example.com', password: 'a-passphrase' });

    expect(getAccessToken()).toBe('access-1');
    expect(getPrincipal()).toEqual({ publicId: 'p1', roles: ['TOURIST'] });
    expect(localStorage.length).toBe(0);
    expect(JSON.stringify(localStorage)).not.toContain('refresh-1');
  });

  it('sends credentials so the refresh cookie is accepted', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        data: {
          access_token: 'a',
          refresh_token: 'r',
          token_type: 'Bearer',
          expires_in: 900,
          principal: { public_id: 'p1', roles: [] },
        },
      }),
      headers: new Headers(),
    });
    vi.stubGlobal('fetch', fetchMock);

    await login({ email: 'alice@example.com', password: 'a-passphrase' });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe('include');
  });
});

describe('loginBannerFor — non-enumeration (TC-013)', () => {
  it('says nothing about which half of the credentials was wrong', () => {
    const banner = loginBannerFor(errorOf('INVALID_CREDENTIALS'));
    expect(banner).toBe('That email address and password do not match.');
  });

  it('never mentions the account existing or not', () => {
    const banner = loginBannerFor(errorOf('INVALID_CREDENTIALS')) ?? '';
    for (const leak of ['no account', 'not found', 'unknown', 'does not exist', 'incorrect password']) {
      expect(banner.toLowerCase()).not.toContain(leak);
    }
  });

  it('reports a lockout distinctly, because that is about the caller', () => {
    expect(loginBannerFor(errorOf('ACCOUNT_LOCKED'))).toContain('locked');
  });

  it('offers verification when the account exists but is unverified', () => {
    expect(loginBannerFor(errorOf('EMAIL_NOT_VERIFIED'))).toContain('Verify');
  });

  it('returns nothing for MFA_REQUIRED so the form reveals the code field', () => {
    expect(loginBannerFor(errorOf('MFA_REQUIRED'))).toBeNull();
    expect(requiresMfa(errorOf('MFA_REQUIRED'))).toBe(true);
  });

  it('ignores non-API errors', () => {
    expect(loginBannerFor(new Error('network'))).toBeNull();
  });
});

describe('fieldErrorsFrom — SRS §24.3', () => {
  it('maps a duplicate registration onto the email field', () => {
    expect(fieldErrorsFrom(errorOf('EMAIL_ALREADY_REGISTERED', 'x', [], 409))).toEqual({
      email: 'This email is already registered.',
    });
  });

  it('maps server details onto their fields', () => {
    const error = errorOf(
      'VALIDATION_ERROR',
      'Invalid',
      [{ field: 'password', code: 'BREACHED', message: 'Choose another.' }],
      422,
    );
    expect(fieldErrorsFrom(error)).toEqual({ password: 'Choose another.' });
  });

  it('keeps every violation, not just the first', () => {
    const error = errorOf(
      'VALIDATION_ERROR',
      'Invalid',
      [
        { field: 'password', code: 'TOO_SHORT', message: 'Too short.' },
        { field: 'email', code: 'INVALID', message: 'Not an address.' },
      ],
      422,
    );
    expect(Object.keys(fieldErrorsFrom(error) ?? {})).toHaveLength(2);
  });

  it('returns null when nothing maps, so the form shows a banner instead', () => {
    // Silently dropping an error the server bothered to send would be worse
    // than an unstyled one.
    expect(fieldErrorsFrom(errorOf('INTERNAL_ERROR', 'x', [], 500))).toBeNull();
  });

  it('ignores non-API errors', () => {
    expect(fieldErrorsFrom(new Error('network'))).toBeNull();
  });
});
