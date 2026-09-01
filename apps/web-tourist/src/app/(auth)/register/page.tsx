'use client';

/**
 * Registration — SRS §24.3.
 *
 *   Validation: names 2–80 characters; email format; password ≥ 12 characters
 *   with the server-side breach check; terms must be accepted; nationality
 *   required.
 *
 * The password rules are checked on the server and echoed here for feedback,
 * never enforced here alone: the minimum length is a `system_setting` value
 * (§30.2) and the breach check needs a corpus the browser must not hold.
 *
 * Social sign-in buttons are absent. §30.2 lists Google and Apple OIDC, but
 * it is deferred — see docs/PHASE-2-PLAN.md Q3 — and a button that cannot
 * work is worse than none.
 */

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@pumba/ui';
import { ConsentCheckbox } from '@/components/auth/consent-checkbox';
import { VerificationDialog } from '@/components/auth/verification-dialog';
import { fieldErrorsFrom, register } from '@/lib/auth';

const MIN_PASSWORD_LENGTH = 12;

export default function RegisterPage() {
  const router = useRouter();
  const [fields, setFields] = useState({
    email: '',
    password: '',
    first_name: '',
    last_name: '',
    nationality: '',
  });
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const update = (name: string, value: string) =>
    setFields((previous) => ({ ...previous, [name]: value }));

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setBanner(null);
    setSubmitting(true);
    try {
      await register({ ...fields, nationality: fields.nationality || undefined });
      setDone(true);
    } catch (caught) {
      const mapped = fieldErrorsFrom(caught);
      if (mapped) setErrors(mapped);
      else setBanner(caught instanceof Error ? caught.message : 'Registration failed.');
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    // §24.3's "Navigation → Email verification notice → Home", as a step the
    // tourist can complete rather than a page telling them to go and look
    // somewhere else. The password is still in this component's state and is
    // handed over so the dialog can sign them in once the code checks out;
    // it goes no further than that.
    return (
      <VerificationDialog
        email={fields.email}
        password={fields.password}
        onVerified={() => router.push('/')}
      />
    );
  }

  const passwordTooShort =
    fields.password.length > 0 && fields.password.length < MIN_PASSWORD_LENGTH;

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-semibold">Create your account</h1>

      {banner && (
        <p role="alert" className="mt-4 rounded-md border border-destructive p-3 text-sm">
          {banner}
        </p>
      )}

      <form className="mt-8 space-y-4" onSubmit={onSubmit} noValidate>
        <Field
          label="First name"
          name="first_name"
          value={fields.first_name}
          onValueChange={update}
          error={errors.first_name}
          minLength={2}
          maxLength={80}
          required
        />
        <Field
          label="Last name"
          name="last_name"
          value={fields.last_name}
          onValueChange={update}
          error={errors.last_name}
          minLength={2}
          maxLength={80}
          required
        />
        <Field
          label="Email"
          name="email"
          type="email"
          value={fields.email}
          onValueChange={update}
          error={errors.email}
          required
        />
        {errors.email?.includes('already registered') && (
          <p className="text-sm">
            <Link href="/login" className="underline">
              Sign in instead
            </Link>
          </p>
        )}
        <Field
          label="Password"
          name="password"
          type="password"
          value={fields.password}
          onValueChange={update}
          error={errors.password}
          hint={`At least ${MIN_PASSWORD_LENGTH} characters. Checked against known breaches.`}
          required
        />
        {passwordTooShort && (
          <p className="text-sm text-destructive">
            {MIN_PASSWORD_LENGTH - fields.password.length} more characters needed.
          </p>
        )}
        <Field
          label="Nationality"
          name="nationality"
          value={fields.nationality}
          onValueChange={update}
          error={errors.nationality}
          maxLength={2}
          hint="Two-letter country code, e.g. DE."
          required
        />

        {/* `pending`, not a link and not silence. `/terms` was a 404, a stub
            under that title would be worse, and "I accept the terms of use."
            with nothing behind it reads as though a document exists. The
            variant is a typed, deliberate state that says otherwise on the
            page; publishing the document turns it into `href` and the notice
            disappears. Release blocker, recorded as item 16 in the phase
            report. */}
        <ConsentCheckbox
          id="terms"
          document={{ name: 'terms of use', pending: true }}
          checked={acceptedTerms}
          onCheckedChange={setAcceptedTerms}
        />

        <Button type="submit" disabled={submitting || !acceptedTerms} className="w-full">
          {submitting ? 'Creating your account…' : 'Create account'}
        </Button>
      </form>

      <p className="mt-6 text-sm text-muted-foreground">
        Already have an account?{' '}
        <Link href="/login" className="underline">
          Sign in
        </Link>
      </p>
    </main>
  );
}

function Field({
  label,
  name,
  value,
  onValueChange,
  error,
  hint,
  type = 'text',
  ...rest
}: {
  label: string;
  name: string;
  value: string;
  onValueChange: (name: string, value: string) => void;
  // `exactOptionalPropertyTypes` is on: these are read straight out of an
  // error map that may not hold the key, so `undefined` is a value they take.
  error?: string | undefined;
  hint?: string | undefined;
  type?: string | undefined;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'value'>) {
  const errorId = `${name}-error`;
  return (
    <div>
      <label htmlFor={name} className="block text-sm font-medium">
        {label}
      </label>
      <input
        id={name}
        name={name}
        type={type}
        value={value}
        onChange={(event) => onValueChange(name, event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className="mt-1 w-full rounded-md border px-3 py-2"
        {...rest}
      />
      {hint && !error && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {error && (
        <p id={errorId} role="alert" className="mt-1 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
