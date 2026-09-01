'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiRequestError } from '@/lib/api';
import { login, resendVerification, verifyEmailCode } from '@/lib/auth';

/**
 * The verification step — SRS §24.3, *"Navigation → Email verification notice
 * → Home"*.
 *
 * The notice used to be a page saying "check your email", which is where the
 * flow stopped: the only thing in that email a person could act on was a
 * 256-bit link, so anybody who registered on a laptop and read the mail on a
 * phone had nowhere to go. Six digits are typeable, and this is where they are
 * typed.
 *
 * **A dialog rather than a route.** The address and password are already in
 * the form's state, and carrying them through a navigation means either a query
 * string — a password in the URL, in history, in the server log — or a store
 * that outlives the moment. Neither is worth it for a step measured in seconds.
 *
 * **It signs the tourist in on success.** They have just proved both halves of
 * the credential: the password on the previous screen and control of the
 * mailbox here. Sending them to a login form to retype what they typed ninety
 * seconds ago would be ceremony, not security. If that sign-in fails they are
 * still verified, so the dialog says so and points at the login page rather
 * than pretending nothing happened.
 *
 * **One message for every rejection**, which mirrors the API: wrong code,
 * expired code and too many attempts are deliberately indistinguishable there,
 * and inventing a distinction here would be the client narrating a state it was
 * not told about.
 */

const DIGITS = 6;

export interface VerificationDialogProps {
  email: string;
  /** Used for the sign-in after verifying; never persisted or logged. */
  password: string;
  onVerified: () => void;
}

type State =
  | { kind: 'entering' }
  | { kind: 'checking' }
  | { kind: 'signing-in' }
  | { kind: 'verified-not-signed-in' }
  | { kind: 'problem'; message: string };

export function VerificationDialog({ email, password, onVerified }: VerificationDialogProps) {
  const [digits, setDigits] = useState<string[]>(Array(DIGITS).fill(''));
  const [state, setState] = useState<State>({ kind: 'entering' });
  const [resent, setResent] = useState(false);
  const boxes = useRef<Array<HTMLInputElement | null>>([]);
  const submitted = useRef<string | null>(null);

  useEffect(() => {
    boxes.current[0]?.focus();
  }, []);

  const code = digits.join('');
  const busy = state.kind === 'checking' || state.kind === 'signing-in';

  const submit = useCallback(
    async (value: string) => {
      // Guarded against the same code being sent twice: the field completes on
      // the last keystroke and again if the user presses the button, and every
      // failed submission spends one of five attempts.
      if (submitted.current === value) return;
      submitted.current = value;

      setState({ kind: 'checking' });
      try {
        await verifyEmailCode(email, value);
      } catch (error) {
        setDigits(Array(DIGITS).fill(''));
        boxes.current[0]?.focus();
        submitted.current = null;
        setState({
          kind: 'problem',
          message:
            error instanceof ApiRequestError
              ? error.message
              : 'That code could not be checked just now.',
        });
        return;
      }

      setState({ kind: 'signing-in' });
      try {
        await login({ email, password });
        onVerified();
      } catch {
        // Verified but not signed in — a real outcome, and one the screen must
        // not report as a failure of the thing that actually worked.
        setState({ kind: 'verified-not-signed-in' });
      }
    },
    [email, password, onVerified],
  );

  function place(index: number, raw: string) {
    const typed = raw.replace(/\D/g, '');
    if (!typed) {
      setDigits((current) => current.map((digit, at) => (at === index ? '' : digit)));
      return;
    }

    // A pasted code fills the row from wherever it was dropped, because people
    // paste all six into the first box.
    setDigits((current) => {
      const next = [...current];
      for (let offset = 0; offset < typed.length && index + offset < DIGITS; offset += 1) {
        next[index + offset] = typed[offset]!;
      }
      const landed = Math.min(index + typed.length, DIGITS - 1);
      boxes.current[landed]?.focus();

      const whole = next.join('');
      if (whole.length === DIGITS && !next.includes('')) void submit(whole);
      return next;
    });
  }

  function onKeyDown(index: number, event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Backspace' && !digits[index] && index > 0) {
      boxes.current[index - 1]?.focus();
    }
    if (event.key === 'ArrowLeft' && index > 0) boxes.current[index - 1]?.focus();
    if (event.key === 'ArrowRight' && index < DIGITS - 1) boxes.current[index + 1]?.focus();
  }

  async function resend() {
    setResent(true);
    setDigits(Array(DIGITS).fill(''));
    submitted.current = null;
    setState({ kind: 'entering' });
    await resendVerification(email);
    boxes.current[0]?.focus();
  }

  if (state.kind === 'verified-not-signed-in') {
    return (
      <Shell title="Your email is verified">
        <p className="text-sm text-muted-foreground">
          We could not sign you in automatically, but your account is ready.
        </p>
        <a
          href="/login"
          className="mt-5 block rounded-md bg-primary px-4 py-2 text-center text-sm font-semibold text-primary-foreground"
        >
          Sign in
        </a>
      </Shell>
    );
  }

  return (
    <Shell title="Enter your code">
      <p className="text-sm text-muted-foreground">
        We sent a six-digit code to <span className="font-medium text-foreground">{email}</span>.
        It expires in fifteen minutes.
      </p>

      <div className="mt-6 flex justify-between gap-2" role="group" aria-label="Verification code">
        {digits.map((digit, index) => (
          <input
            key={index}
            ref={(element) => {
              boxes.current[index] = element;
            }}
            value={digit}
            onChange={(event) => place(index, event.target.value)}
            onKeyDown={(event) => onKeyDown(index, event)}
            disabled={busy}
            inputMode="numeric"
            autoComplete={index === 0 ? 'one-time-code' : 'off'}
            aria-label={`Digit ${index + 1}`}
            maxLength={DIGITS}
            className="h-14 w-full rounded-md border border-border bg-background text-center text-xl font-semibold tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          />
        ))}
      </div>

      {state.kind === 'problem' ? (
        <p role="alert" className="mt-4 text-sm text-destructive-ink">
          {state.message}
        </p>
      ) : null}

      <button
        type="button"
        disabled={busy || code.length < DIGITS}
        onClick={() => void submit(code)}
        className="mt-6 w-full rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60"
      >
        {state.kind === 'checking'
          ? 'Checking…'
          : state.kind === 'signing-in'
            ? 'Signing you in…'
            : 'Verify'}
      </button>

      <p className="mt-4 text-center text-sm text-muted-foreground">
        {resent ? (
          'A new code is on its way.'
        ) : (
          <>
            No code?{' '}
            <button
              type="button"
              onClick={() => void resend()}
              className="font-medium text-primary hover:underline"
            >
              Send another
            </button>
          </>
        )}
      </p>
    </Shell>
  );
}

/**
 * `role="dialog"` with `aria-modal`, and focus sent into it on mount.
 *
 * There is nothing behind it to return to — registration has succeeded and the
 * form is gone — so it has no dismiss control. A close button here would leave
 * a tourist with an account they cannot use and no way back to this screen.
 */
function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="verification-title"
        className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg"
      >
        <h2 id="verification-title" className="font-display text-xl font-bold tracking-tight">
          {title}
        </h2>
        <div className="mt-2">{children}</div>
      </div>
    </div>
  );
}
