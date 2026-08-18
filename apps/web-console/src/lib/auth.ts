/**
 * Console authentication — SRS §30.2, §37.2.
 *
 * The console differs from the tourist site in one way that matters: every
 * role that can reach it has **mandatory** TOTP (§30.2 — PROVIDER_* and all
 * administrative roles). So `MFA_REQUIRED` is not an edge case here, it is
 * the normal second step, and an account that has never enrolled cannot get
 * in at all — §41.2: "A provider or administrator cannot access their console
 * without TOTP."
 */

import { API_BASE_URL, ApiRequestError, apiFetch } from './api';
import { discardRefreshToken, setAccessToken, setPrincipal } from './session';

export interface LoginInput {
  email: string;
  password: string;
  mfa_code?: string | undefined;
}

interface LoginResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  principal: { public_id: string; roles: string[] };
}

export async function login(input: LoginInput): Promise<LoginResponse> {
  const result = await apiFetch<LoginResponse>('/auth/login', {
    method: 'POST',
    body: input,
    credentials: 'include',
  });
  setAccessToken(result.access_token, result.expires_in);
  setPrincipal({ publicId: result.principal.public_id, roles: result.principal.roles });
  discardRefreshToken(result.refresh_token);
  return result;
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, { method: 'POST', credentials: 'include' });
}

/** The account must enrol before it can sign in at all — §30.2. */
export function needsEnrolment(error: unknown): boolean {
  return (
    error instanceof ApiRequestError &&
    error.code === 'MFA_REQUIRED' &&
    error.details.some(
      (detail) => (detail as { code?: string }).code === 'MFA_ENROLMENT_REQUIRED',
    )
  );
}

/** A code is needed, and the account already has one to give. */
export function needsCode(error: unknown): boolean {
  return (
    error instanceof ApiRequestError && error.code === 'MFA_REQUIRED' && !needsEnrolment(error)
  );
}

export function bannerFor(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return 'Sign-in failed.';
  switch (error.code) {
    case 'INVALID_CREDENTIALS':
      // Non-enumerating, exactly as on the tourist site (TC-013).
      return 'That email address and password do not match.';
    case 'ACCOUNT_LOCKED':
      return 'Too many attempts. This account is temporarily locked.';
    case 'ACCOUNT_SUSPENDED':
      return 'This account is suspended.';
    default:
      return error.message;
  }
}
