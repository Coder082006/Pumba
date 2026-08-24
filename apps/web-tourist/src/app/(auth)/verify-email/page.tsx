import { Suspense } from 'react';

import { VerifyEmail } from '@/components/auth/verify-email';

/**
 * Email verification — SRS §24.4.
 *
 * The verification itself is in `components/auth/verify-email.tsx` because it
 * reads the token with `useSearchParams`, and Next refuses to prerender a page
 * that does so outside a Suspense boundary — the query string is not known at
 * build time, so the whole route would silently opt out of static rendering
 * without one. The boundary is here; the work is there.
 *
 * The fallback carries the same heading shape as every resolved state, so the
 * page does not jump when the effect settles (§29, CLS).
 */
export const metadata = {
  title: 'Verify your email',
  robots: { index: false, follow: false },
};

export default function VerifyEmailPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <Suspense
        fallback={
          <>
            <h1 className="text-2xl font-semibold">Verifying your email…</h1>
            <p className="mt-3 text-muted-foreground">One moment.</p>
          </>
        }
      >
        <VerifyEmail />
      </Suspense>
    </main>
  );
}
