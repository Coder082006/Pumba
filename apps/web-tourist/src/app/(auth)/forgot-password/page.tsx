'use client';

/**
 * Forgot password — SRS §24.5.
 *
 *   States: the success message is identical whether or not the address
 *   exists, to avoid account enumeration; expired-token errors offer to
 *   restart.
 *
 * The server already returns the same 202 either way. This screen has to
 * hold the same line: any difference in what it renders — a different
 * message, a different delay, a disabled button — would leak the answer the
 * API was careful not to give.
 */

import Link from 'next/link';
import { useState } from 'react';
import { Button } from '@pumba/ui';
import { ApiRequestError } from '@/lib/api';
import { requestPasswordReset, resetPassword } from '@/lib/auth';

const IDENTICAL_MESSAGE =
  'If that address has an account, a reset link is on its way. Check your inbox and spam folder.';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setRateLimited(false);
    try {
      await requestPasswordReset(email);
    } catch (caught) {
      // Only a rate limit is surfaced, because it is about the *caller*, not
      // about whether the address exists. Every other failure renders the
      // same confirmation — an error here would be an enumeration oracle.
      if (caught instanceof ApiRequestError && caught.code === 'RATE_LIMITED') {
        setRateLimited(true);
      }
    } finally {
      setSubmitting(false);
      setSent(true);
    }
  }

  if (sent && !rateLimited) {
    return (
      <main className="mx-auto max-w-md px-4 py-16">
        <h1 className="text-2xl font-semibold">Check your email</h1>
        <p className="mt-3 text-muted-foreground">{IDENTICAL_MESSAGE}</p>
        <p className="mt-6 text-sm">
          <Link href="/login" className="underline">
            Back to sign in
          </Link>
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-semibold">Reset your password</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Enter your email address and we will send you a link.
      </p>

      {rateLimited && (
        <p role="alert" className="mt-4 rounded-md border border-destructive p-3 text-sm">
          Too many requests. Wait a few minutes before trying again.
        </p>
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
        <Button type="submit" disabled={submitting} className="w-full">
          {submitting ? 'Sending…' : 'Send reset link'}
        </Button>
      </form>
    </main>
  );
}

/**
 * The second step, reached from the emailed deep link.
 *
 * Exported rather than routed for now: the link format is settled with the
 * notification templates in the notify phase, and guessing at it here would
 * mean building a route that has to change.
 */
export function ResetPasswordForm({ token }: { token: string }) {
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [done, setDone] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (password !== confirmation) {
      setError('Those passwords do not match.');
      return;
    }
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.status === 422) {
        setExpired(true);
        setError(caught.message);
        return;
      }
      setError(caught instanceof Error ? caught.message : 'Could not reset the password.');
    }
  }

  if (done) {
    return (
      <p>
        Your password has been changed.{' '}
        <Link href="/login" className="underline">
          Sign in
        </Link>
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {error && (
        <div role="alert" className="rounded-md border border-destructive p-3 text-sm">
          <p>{error}</p>
          {expired && (
            <p className="mt-1">
              <Link href="/forgot-password" className="underline">
                Request a new link
              </Link>
            </p>
          )}
        </div>
      )}
      <input
        type="password"
        aria-label="New password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        className="w-full rounded-md border px-3 py-2"
      />
      <input
        type="password"
        aria-label="Confirm new password"
        value={confirmation}
        onChange={(event) => setConfirmation(event.target.value)}
        className="w-full rounded-md border px-3 py-2"
      />
      <Button type="submit" className="w-full">
        Set new password
      </Button>
    </form>
  );
}
