'use client';

/**
 * Email verification — SRS §24.4.
 *
 * Built because the login screen already linked here and this route did not
 * exist. A user whose account was unverified was told to click a link that
 * 404'd, and there was no other way to reach verification from the web
 * client at all — the token in the email had nowhere to be spent.
 *
 * **The call runs from an effect, not from the render.** A server component
 * could POST during rendering, but the token arrives in a URL that a link
 * prefetcher, a mail-scanner or a corporate proxy may fetch before the user
 * ever clicks — and `/auth/verify-email` is single-use, so a prefetch would
 * consume it and the real click would land on "already used". An effect only
 * runs in a browser that actually rendered the page.
 */

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';

import { ApiRequestError } from '@/lib/api';
import { verifyEmail } from '@/lib/auth';

type State = 'missing' | 'working' | 'done' | 'expired' | 'failed';

export function VerifyEmail() {
  const token = useSearchParams().get('token');
  const [state, setState] = useState<State>(token ? 'working' : 'missing');
  // React 19 runs effects twice in development. The token is single-use, so a
  // second call would report "expired" over a verification that succeeded.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    verifyEmail(token)
      .then(() => setState('done'))
      .catch((caught: unknown) => {
        const expired =
          caught instanceof ApiRequestError &&
          (caught.code === 'TOKEN_EXPIRED' || caught.code === 'TOKEN_INVALID');
        setState(expired ? 'expired' : 'failed');
      });
  }, [token]);

  return (
    <>
      <h1 className="text-2xl font-semibold">{TITLES[state]}</h1>
      <p className="mt-3 text-muted-foreground" role={state === 'working' ? 'status' : undefined}>
        {MESSAGES[state]}
      </p>
      {state === 'working' ? null : (
        <p className="mt-6 text-sm">
          <Link href="/login" className="underline">
            Go to sign in
          </Link>
        </p>
      )}
    </>
  );
}

const TITLES: Record<State, string> = {
  missing: 'No verification link',
  working: 'Verifying your email…',
  done: 'Your email is verified',
  expired: 'That link has expired',
  failed: 'We could not verify that link',
};

const MESSAGES: Record<State, string> = {
  // Says what to do rather than what went wrong: somebody who navigated here
  // by hand needs the email, not an explanation of query parameters.
  missing: 'Open the link in the verification email we sent you.',
  working: 'One moment.',
  done: 'You can sign in now.',
  // No "send me another" button: there is no resend endpoint on the API
  // (`apps/identity/urls.py` has `auth/verify-email` and nothing else), and
  // offering one that does nothing is how the broken link on the sign-in
  // screen came to exist in the first place.
  expired: 'Verification links are single-use and time-limited. Sign in again to get a new one.',
  failed: 'Please try the link from your email again, or sign in to request a new one.',
};
