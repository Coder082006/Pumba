'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { ensureSession } from '@/lib/auth';
import { getAccessToken } from '@/lib/session';

/**
 * Everything a tourist's trip touches sits behind this — SRS §24.14, §30.3.
 *
 * The catalogue stays public. §24.7–24.9 make the destination, attraction and
 * activity pages the surface a search engine indexes and a tourist reads before
 * they have an account, and gating those would remove the reason anybody
 * arrives. What is gated is what belongs to *a person*: their trips, the
 * planner, the flights, the summary.
 *
 * **This is a courtesy, not the control.** The API decides: every trip endpoint
 * filters by owner and answers a stranger with 404 rather than 403 (§30.3), so
 * a signed-out request gets nothing whether or not this component exists. What
 * it prevents is a tourist landing on a screen that renders an error where an
 * explanation belongs.
 *
 * **It tries the refresh cookie before giving up.** ADR 0008 keeps the access
 * token in a module variable and never persists it, so a reload always starts
 * with nothing in memory — the cookie is the only thing that survives. A guard
 * that read `getAccessToken()` and redirected on null would sign the tourist
 * out on every refresh, which is precisely what the app did before
 * `refreshSession` had a caller.
 *
 * **Through `ensureSession`, never `refreshSession` directly.** The header's
 * `AccountMenu` also needs the session, and both mount on this page. Refresh
 * rotates — presenting a superseded token is read as theft and revokes the
 * whole family — so two unguarded calls would race and sign the tourist out of
 * their own account. `ensureSession` gives every caller the same promise.
 */
export function RequireSignIn({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [state, setState] = useState<'checking' | 'in' | 'out'>(() =>
    getAccessToken() ? 'in' : 'checking',
  );

  useEffect(() => {
    if (state !== 'checking') return;
    void (async () => {
      setState((await ensureSession()) ? 'in' : 'out');
    })();
  }, [state]);

  if (state === 'checking') {
    return (
      <div aria-hidden className="space-y-4">
        <div className="h-8 w-1/3 rounded-md bg-muted" />
        <div className="h-40 rounded-lg bg-muted" />
      </div>
    );
  }

  if (state === 'out') {
    return (
      <div className="mx-auto max-w-md rounded-lg border border-border p-8 text-center">
        <h1 className="font-display text-2xl font-bold tracking-tight">Sign in to plan a trip</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Your trips are private to your account. Browsing Zanzibar needs no account at all —
          this part does.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:justify-center">
          {/*
            `next` carries where they were headed, so signing in returns them
            to the screen they asked for rather than to a generic home page.
          */}
          <Link
            href={`/login?next=${encodeURIComponent(pathname)}`}
            className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90"
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className="rounded-md border border-border px-6 py-3 text-sm font-semibold transition-colors duration-fast ease-out hover:bg-muted"
          >
            Create an account
          </Link>
        </div>
        <p className="mt-6 text-sm text-muted-foreground">
          <Link href="/explore" className="text-primary hover:underline">
            Keep exploring
          </Link>{' '}
          in the meantime.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
