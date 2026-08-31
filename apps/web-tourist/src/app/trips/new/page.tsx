'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useState } from 'react';

import { ApiRequestError } from '@/lib/api';
import { createTrip } from '@/lib/trips';

/**
 * Plan a trip — SRS §41.3, the first sentence of Phase 4's acceptance.
 *
 * *"A tourist can create a trip, set dates and party, record inbound and
 * outbound flights, add stays, activities and attractions, and generate an
 * itinerary."* Every screen after this one existed before this one did, which
 * meant a signed-in tourist reached an empty list and stopped.
 *
 * **Validation here is shape only, and the omission is deliberate.** The
 * service owns "a trip cannot start in the past" (TC-031) and `trip.max_days`,
 * and it answers both against the *destination's* date and a `system_setting`.
 * A copy in the browser would use the device's clock and a hard-coded ceiling,
 * and would disagree the first time somebody in Zanzibar planned a trip at
 * eleven at night or an administrator changed the limit. So the form checks
 * that the fields are filled and lets the server say what they mean.
 */

function NewTripForm() {
  const router = useRouter();
  const params = useSearchParams();

  // Arriving from a destination page carries the subject with it, which is
  // where somebody actually decides to go.
  const [destination, setDestination] = useState(params.get('destination') ?? '');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [adults, setAdults] = useState('2');
  const [children, setChildren] = useState('0');
  const [infants, setInfants] = useState('0');
  const [title, setTitle] = useState('');

  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = useCallback(async () => {
    setBusy(true);
    setProblem(null);
    try {
      const trip = await createTrip({
        destination,
        start_date: startDate,
        end_date: endDate,
        adults: Number(adults || '1'),
        children: Number(children || '0'),
        infants: Number(infants || '0'),
        ...(title ? { title } : {}),
      });
      router.push(`/trips/${trip.public_id}`);
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 401) {
        setProblem('Sign in first — your trips are private to your account.');
      } else {
        setProblem(
          error instanceof ApiRequestError
            ? error.message
            : 'That trip could not be created just now.',
        );
      }
      setBusy(false);
    }
  }, [destination, startDate, endDate, adults, children, infants, title, router]);

  const incomplete = !destination || !startDate || !endDate;
  const field = 'mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm';

  return (
    <div className="max-w-xl space-y-8">
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <Link href="/trips" className="hover:underline">
            Your trips
          </Link>
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">Plan a trip</h1>
        <p className="text-sm text-muted-foreground">
          Dates and who is travelling. You can add stays and activities next.
        </p>
      </header>

      {problem ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive-ink">
          {problem}
        </p>
      ) : null}

      <div className="space-y-4">
        <label className="block text-sm">
          Where
          <input
            className={field}
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            placeholder="stone-town"
          />
          <span className="mt-1 block text-xs text-muted-foreground">
            The destination&rsquo;s name in the address bar — open it from{' '}
            <Link href="/explore" className="text-primary hover:underline">
              Explore
            </Link>{' '}
            and use &ldquo;Plan a trip here&rdquo; to fill this in.
          </span>
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm">
            Arriving
            <input
              type="date"
              className={field}
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Leaving
            <input
              type="date"
              className={field}
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </label>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <label className="text-sm">
            Adults
            <input
              className={field}
              inputMode="numeric"
              value={adults}
              onChange={(e) => setAdults(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Children
            <input
              className={field}
              inputMode="numeric"
              value={children}
              onChange={(e) => setChildren(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Infants
            <input
              className={field}
              inputMode="numeric"
              value={infants}
              onChange={(e) => setInfants(e.target.value)}
            />
          </label>
        </div>

        <label className="block text-sm">
          Call it something (optional)
          <input
            className={field}
            value={title}
            maxLength={140}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Honeymoon"
          />
        </label>
      </div>

      <button
        type="button"
        disabled={incomplete || busy}
        onClick={() => void submit()}
        className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60"
      >
        {busy ? 'Creating…' : 'Start planning'}
      </button>
    </div>
  );
}

export default function NewTripPage() {
  // `useSearchParams` needs a Suspense boundary; without one the whole route
  // opts into client rendering at build time and Next says so as an error.
  return (
    <Suspense fallback={<div aria-hidden className="h-96 rounded-lg bg-muted" />}>
      <NewTripForm />
    </Suspense>
  );
}
