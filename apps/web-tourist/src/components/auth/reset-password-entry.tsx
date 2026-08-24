'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';

import { ResetPasswordForm } from '@/components/auth/reset-password-form';

/**
 * Reads the token out of the deep link and hands it to the form.
 *
 * Split from the route file because `useSearchParams` outside a Suspense
 * boundary makes Next refuse to prerender the page, and split from
 * `ResetPasswordForm` because that component takes a `token` it can rely on —
 * the "no token at all" case is a different screen, not a form state.
 */
export function ResetPasswordEntry() {
  const token = useSearchParams().get('token');

  if (!token) {
    return (
      <div className="mt-6 space-y-3 text-sm">
        {/* Says what to do rather than what is wrong. Somebody reaching this
            without a token has typed the URL or followed a truncated link, and
            what they need is the way to get a fresh one. */}
        <p className="text-muted-foreground">
          This link is incomplete. Password reset links expire and can only be used once.
        </p>
        <p>
          <Link href="/forgot-password" className="underline">
            Request a new link
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="mt-6">
      <ResetPasswordForm token={token} />
    </div>
  );
}
