/**
 * Console authentication tests — SRS §30.2, §41.2.
 *
 * "A provider or administrator cannot access their console without TOTP."
 * That sentence is the reason this file exists: the console has to tell apart
 * three states the tourist site never sees — enrolled and asked for a code,
 * enrolled and gave the wrong one, and never enrolled at all.
 */

import { describe, expect, it } from 'vitest';

import { ApiRequestError } from '../api';
import { bannerFor, needsCode, needsEnrolment } from '../auth';

function errorOf(code: string, details: unknown[] = [], message = 'x', status = 401) {
  return new ApiRequestError(status, {
    code,
    message,
    details,
    request_id: null,
    retryable: false,
  } as never);
}

describe('mandatory TOTP', () => {
  it('detects an account that has never enrolled', () => {
    const error = errorOf('MFA_REQUIRED', [{ code: 'MFA_ENROLMENT_REQUIRED' }]);
    expect(needsEnrolment(error)).toBe(true);
    expect(needsCode(error)).toBe(false);
  });

  it('detects an account that simply has not been asked for a code', () => {
    const error = errorOf('MFA_REQUIRED');
    expect(needsCode(error)).toBe(true);
    expect(needsEnrolment(error)).toBe(false);
  });

  it('does not treat a wrong password as an MFA state', () => {
    expect(needsCode(errorOf('INVALID_CREDENTIALS'))).toBe(false);
    expect(needsEnrolment(errorOf('INVALID_CREDENTIALS'))).toBe(false);
  });

  it('ignores non-API errors', () => {
    expect(needsCode(new Error('network'))).toBe(false);
    expect(needsEnrolment(new Error('network'))).toBe(false);
  });
});

describe('bannerFor — non-enumeration holds here too', () => {
  it('gives one message for any bad credential', () => {
    expect(bannerFor(errorOf('INVALID_CREDENTIALS'))).toBe(
      'That email address and password do not match.',
    );
  });

  it('never reveals whether the account exists', () => {
    const banner = bannerFor(errorOf('INVALID_CREDENTIALS')).toLowerCase();
    for (const leak of ['no account', 'not found', 'unknown user', 'wrong password']) {
      expect(banner).not.toContain(leak);
    }
  });

  it('reports a lockout', () => {
    expect(bannerFor(errorOf('ACCOUNT_LOCKED'))).toContain('locked');
  });

  it('reports a suspension', () => {
    expect(bannerFor(errorOf('ACCOUNT_SUSPENDED'))).toContain('suspended');
  });

  it('falls back to the server message for anything else', () => {
    expect(bannerFor(errorOf('SOMETHING_NEW', [], 'A new failure.'))).toBe('A new failure.');
  });

  it('has a message for a non-API failure', () => {
    expect(bannerFor(new Error('network'))).toBe('Sign-in failed.');
  });
});
