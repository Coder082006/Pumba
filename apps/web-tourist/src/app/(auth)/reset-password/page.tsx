import { Suspense } from 'react';

import { ResetPasswordEntry } from '@/components/auth/reset-password-entry';

/**
 * Set a new password — SRS §24.5, second step.
 *
 * `ResetPasswordForm` has existed since Phase 2 and nothing ever mounted it,
 * so `/forgot-password` sent an email whose link went nowhere. That is the
 * same defect as the missing `/verify-email` route, and it survived for the
 * same reason: the component's own docstring deferred routing until "the
 * deep-link format is settled with the notification templates".
 *
 * The format is not something to wait for. It is ours to choose, no template
 * exists to disagree with, and the deferral left a live flow dead in the
 * meantime — which is precisely the trade that let the verification route stay
 * missing through a phase sign-off. `?token=…` matches `/verify-email` so the
 * two emails carry the same shape, and settling it here is what the notify
 * phase will build against.
 *
 * `robots.ts` disallows this path: the token is single-use, so a crawler
 * following the link out of an email would consume it.
 */
export const metadata = {
  title: 'Set a new password',
  robots: { index: false, follow: false },
};

export default function ResetPasswordPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-semibold">Set a new password</h1>
      <Suspense fallback={<p className="mt-6 text-sm text-muted-foreground">One moment.</p>}>
        <ResetPasswordEntry />
      </Suspense>
    </main>
  );
}
