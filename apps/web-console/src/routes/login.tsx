/**
 * Console sign-in — SRS §24.4 adapted for §30.2's mandatory TOTP.
 *
 * A provider or administrator account that has not enrolled is refused here
 * rather than admitted to a reduced console. §41.2: "A provider or
 * administrator cannot access their console without TOTP."
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { bannerFor, login, needsCode, needsEnrolment } from '../lib/auth';

export function ConsoleLogin() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [showCode, setShowCode] = useState(false);
  const [enrolmentRequired, setEnrolmentRequired] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBanner(null);
    setSubmitting(true);
    try {
      const result = await login({ email, password, mfa_code: mfaCode || undefined });
      const isAdmin = result.principal.roles.some((role) => role.endsWith('_ADMIN'));
      navigate(isAdmin ? '/admin' : '/provider', { replace: true });
    } catch (caught) {
      if (needsEnrolment(caught)) {
        setEnrolmentRequired(true);
        return;
      }
      if (needsCode(caught)) {
        setShowCode(true);
        setBanner(mfaCode ? 'That code is not valid. Try the current one.' : null);
        return;
      }
      setBanner(bannerFor(caught));
    } finally {
      setSubmitting(false);
    }
  }

  if (enrolmentRequired) {
    return (
      <main className="mx-auto max-w-md px-4 py-16">
        <h1 className="text-2xl font-semibold">Two-factor authentication required</h1>
        <p className="mt-3 text-muted-foreground">
          Provider and administrator accounts must use an authenticator app. Enrol from the
          link we have emailed you, then sign in again.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <h1 className="text-2xl font-semibold">Console sign-in</h1>

      {banner && (
        <p role="alert" className="mt-4 rounded-md border border-destructive p-3 text-sm">
          {banner}
        </p>
      )}

      <form className="mt-8 space-y-4" onSubmit={onSubmit} noValidate>
        <div>
          <label htmlFor="email" className="block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            className="mt-1 w-full rounded-md border px-3 py-2"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            className="mt-1 w-full rounded-md border px-3 py-2"
          />
        </div>

        {showCode && (
          <div>
            <label htmlFor="mfa_code" className="block text-sm font-medium">
              Authenticator code
            </label>
            <input
              id="mfa_code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={mfaCode}
              onChange={(event) => setMfaCode(event.target.value)}
              required
              className="mt-1 w-full rounded-md border px-3 py-2"
            />
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-primary px-4 py-2 text-primary-foreground disabled:opacity-50"
        >
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  );
}
