'use client';

/**
 * Login — SRS §24.4.
 *
 *   States: error banner for 401 using a single non-enumerating message; 423
 *   shows the lockout expiry time; 403 EMAIL_NOT_VERIFIED offers to resend
 *   verification.
 *
 * The non-enumerating message is the whole point of TC-013 reaching the
 * interface: the server returns identical bodies for an unknown address and a
 * wrong password, and a screen that said "no account with that email" would
 * undo that at the last step.
 */

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@pumba/ui';
import { ApiRequestError } from '@/lib/api';
import { login, loginBannerFor, requiresMfa } from '@/lib/auth';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [showMfa, setShowMfa] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [unlockSeconds, setUnlockSeconds] = useState<number | null>(null);
  const [offerResend, setOfferResend] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBanner(null);
    setUnlockSeconds(null);
    setOfferResend(false);
    setSubmitting(true);
    try {
      await login({ email, password, mfa_code: mfaCode || undefined });
      router.push('/');
    } catch (caught) {
      if (requiresMfa(caught)) {
        // Not an error state: the account has TOTP and simply has not been
        // asked for a code yet.
        setShowMfa(true);
        setBanner(
          mfaCode ? 'That code is not valid. Try the current one.' : null,
        );
        return;
      }
      if (caught instanceof ApiRequestError) {
        if (caught.code === 'ACCOUNT_LOCKED') {
          const seconds = (caught.details[0] as { retry_after_seconds?: number } | undefined)
            ?.retry_after_seconds;
          setUnlockSeconds(seconds ?? null);
        }
        if (caught.code === 'EMAIL_NOT_VERIFIED') setOfferResend(true);
      }
      setBanner(loginBannerFor(caught) ?? 'Sign-in failed.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-semibold">Sign in</h1>

      {banner && (
        <div role="alert" className="mt-4 rounded-md border border-destructive p-3 text-sm">
          <p>{banner}</p>
          {unlockSeconds !== null && (
            <p className="mt-1">Try again in about {Math.ceil(unlockSeconds / 60)} minutes.</p>
          )}
          {offerResend && (
            /* Not a "resend" link. `apps/identity/urls.py` exposes
               `auth/verify-email` and no resend endpoint, so a control
               offering to send another email would do nothing — which is the
               defect this replaces: the old link pointed at a route that did
               not exist either. It says what actually works instead. */
            <p className="mt-1">
              Check your inbox for the verification email — its link finishes the job.
            </p>
          )}
        </div>
      )}

      <form className="mt-8 space-y-4" onSubmit={onSubmit} noValidate>
        <div>
          <label htmlFor="email" className="block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            className="mt-1 w-full rounded-md border px-3 py-2"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            className="mt-1 w-full rounded-md border px-3 py-2"
          />
        </div>

        {showMfa && (
          <div>
            <label htmlFor="mfa_code" className="block text-sm font-medium">
              Authenticator code
            </label>
            <input
              id="mfa_code"
              name="mfa_code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={mfaCode}
              onChange={(event) => setMfaCode(event.target.value)}
              className="mt-1 w-full rounded-md border px-3 py-2"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Six digits from your authenticator app.
            </p>
          </div>
        )}

        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>

      <div className="mt-6 space-y-2 text-sm text-muted-foreground">
        <p>
          <Link href="/forgot-password" className="underline">
            Forgot your password?
          </Link>
        </p>
        <p>
          No account?{' '}
          <Link href="/register" className="underline">
            Create one
          </Link>
        </p>
      </div>
    </main>
  );
}
