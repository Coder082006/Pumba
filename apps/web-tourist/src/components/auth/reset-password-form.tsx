'use client';

/**
 * The second step of the §24.5 reset, reached from the emailed deep link.
 *
 * It lives here rather than beside the page because a `page.tsx` in the App
 * Router may export only the default component and Next's own known fields —
 * `metadata`, `generateMetadata`, `dynamic` and the rest. A second named
 * export makes the route fail type checking with "is not a valid Page export
 * field", which is what CI caught.
 *
 * Not routed yet: the deep-link format is settled with the notification
 * templates in the notify phase, and guessing at it here would mean building a
 * route that has to change.
 */

import Link from 'next/link';
import { useState } from 'react';
import { Button } from '@pumba/ui';
import { ApiRequestError } from '@/lib/api';
import { resetPassword } from '@/lib/auth';

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
