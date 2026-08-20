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
import { requestPasswordReset } from '@/lib/auth';

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
