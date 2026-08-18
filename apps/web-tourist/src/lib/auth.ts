/**
 * Authentication calls — SRS §9.4.1, §9.4.2, §24.3, §24.4, §24.5.
 *
 * `credentials: 'include'` on every one of these, and only these: the refresh
 * cookie is scoped to /api/v1/auth (ADR 0008) and the rest of the app has no
 * business sending it.
 */

import { API_BASE_URL, ApiRequestError, apiFetch } from './api';
import { discardRefreshToken, setAccessToken, setPrincipal } from './session';

export interface RegisterInput {
  email: string;
  password: string;
  first_name: string;
  last_name: string;
  nationality?: string | undefined;
  preferred_currency?: string | undefined;
  marketing_opt_in?: boolean | undefined;
}

export interface LoginInput {
  email: string;
  password: string;
  mfa_code?: string | undefined;
}

interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  principal: { public_id: string; roles: string[] };
}

interface RegisterResponse {
  user: { public_id: string; email: string; status: string };
  verification_required: boolean;
}

async function authFetch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body,
    // Lets the browser accept the Set-Cookie the server issues for a known
    // web origin, and send it back on refresh.
    credentials: 'include',
  });
}

export async function register(input: RegisterInput): Promise<RegisterResponse> {
  return authFetch<RegisterResponse>('/auth/register', input);
}

export async function login(input: LoginInput): Promise<LoginResponse> {
  const result = await authFetch<LoginResponse>('/auth/login', input);
  setAccessToken(result.access_token, result.expires_in);
  setPrincipal({ publicId: result.principal.public_id, roles: result.principal.roles });
  // The cookie is the browser's copy; the body copy is for the mobile
  // clients and is dropped here on purpose (ADR 0008).
  discardRefreshToken(result.refresh_token);
  return result;
}

export async function requestPasswordReset(email: string): Promise<void> {
  await authFetch<{ message: string }>('/auth/password/forgot', { email });
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  await authFetch<null>('/auth/password/reset', { token, new_password: newPassword });
}

export async function verifyEmail(token: string): Promise<void> {
  await authFetch<unknown>('/auth/verify-email', { token });
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE_URL}/auth/logout`, { method: 'POST', credentials: 'include' });
}

/**
 * Map an API failure onto the field it belongs to, per SRS §24.3.
 *
 * The screen places errors "to fields by the `details[].field` path", so the
 * mapping lives here rather than in each form. A code with no field mapping
 * returns `null`, which the form renders as a banner — silently dropping an
 * error the server bothered to send would be worse than an unstyled one.
 */
export function fieldErrorsFrom(error: unknown): Record<string, string> | null {
  if (!(error instanceof ApiRequestError)) return null;

  if (error.code === 'EMAIL_ALREADY_REGISTERED') {
    // §24.3: "409 maps to a field-level 'this email is already registered'
    // with a login link."
    return { email: 'This email is already registered.' };
  }

  if (error.details.length === 0) return null;

  const mapped: Record<string, string> = {};
  for (const detail of error.details) {
    const field = (detail as { field?: string }).field;
    const message =
      (detail as { message?: string }).message ?? (detail as { issue?: string }).issue;
    if (field && message) mapped[field] = message;
  }
  return Object.keys(mapped).length > 0 ? mapped : null;
}

/** §24.4: a single non-enumerating message for every 401 on login. */
export function loginBannerFor(error: unknown): string | null {
  if (!(error instanceof ApiRequestError)) return null;
  switch (error.code) {
    case 'INVALID_CREDENTIALS':
      // Deliberately says nothing about which half was wrong, and nothing
      // about whether the account exists (TC-013).
      return 'That email address and password do not match.';
    case 'ACCOUNT_LOCKED':
      return 'Too many attempts. This account is temporarily locked.';
    case 'EMAIL_NOT_VERIFIED':
      return 'Verify your email address to continue. We can resend the link.';
    case 'ACCOUNT_SUSPENDED':
      return 'This account is suspended. Contact support.';
    case 'MFA_REQUIRED':
      return null; // The form reveals the code field instead of erroring.
    default:
      return error.message;
  }
}

export function requiresMfa(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === 'MFA_REQUIRED';
}
