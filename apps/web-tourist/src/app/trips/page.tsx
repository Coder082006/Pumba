'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Money } from '@pumba/ui';

import { ApiRequestError } from '@/lib/api';
import { listTrips, type TripSummary } from '@/lib/trips';

/**
 * My Trips — SRS §24.20's entry point.
 *
 * A client component, and not by preference. `lib/session` keeps the access
 * token in memory and never persists it (ADR 0008), so there is no cookie a
 * server render could authenticate with. The catalogue pages stay
 * server-rendered because §24.8 makes them an SEO surface; a tourist's own
 * trips are nobody's search result.
 *
 * The list renders `TripSummary`, which the API deliberately made narrower than
 * `Trip`: no itinerary, no flights. Rendering the detail shape here would load
 * a fortnight of items per card and teach this page to depend on fields the
 * list endpoint will later stop sending.
 */

type State =
  | { status: 'loading' }
  | { status: 'ready'; trips: TripSummary[] }
  | { status: 'signed-out' }
  | { status: 'error'; message: string };

export default function MyTripsPage() {
  const [state, setState] = useState<State>({ status: 'loading' });

  const load = useCallback(async () => {
    setState({ status: 'loading' });
    try {
      setState({ status: 'ready', trips: await listTrips() });
    } catch (error) {
      // 401 is not a failure to report as one: it means "sign in", which is an
      // instruction rather than an error, and rendering it as a red box would
      // be alarming for the ordinary case of arriving with no session.
      if (error instanceof ApiRequestError && error.status === 401) {
        setState({ status: 'signed-out' });
        return;
      }
      setState({
        status: 'error',
        message:
          error instanceof ApiRequestError
            ? error.message
            : 'Your trips could not be loaded just now.',
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="font-display text-3xl font-bold tracking-tight">Your trips</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every journey you have started planning.
        </p>
      </header>

      {state.status === 'loading' ? (
        <div aria-hidden className="space-y-3">
          {[0, 1].map((n) => (
            <div key={n} className="h-24 rounded-lg bg-muted" />
          ))}
        </div>
      ) : null}

      {state.status === 'signed-out' ? (
        <p className="rounded-lg border border-border bg-muted p-6 text-sm">
          <Link href="/login" className="font-medium text-primary hover:underline">
            Sign in
          </Link>{' '}
          to see the trips you have planned.
        </p>
      ) : null}

      {state.status === 'error' ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-6 text-sm">
          <p className="text-destructive-ink">{state.message}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
          >
            Try again
          </button>
        </div>
      ) : null}

      {state.status === 'ready' && state.trips.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-8 text-center">
          <p className="text-muted-foreground">You have not planned a trip yet.</p>
          <Link
            href="/explore"
            className="mt-4 inline-block rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90"
          >
            Start exploring
          </Link>
        </div>
      ) : null}

      {state.status === 'ready' && state.trips.length > 0 ? (
        <ul className="space-y-3">
          {state.trips.map((trip) => (
            <li key={trip.public_id}>
              <Link
                href={`/trips/${trip.public_id}`}
                className="block rounded-lg border border-border p-4 transition-colors duration-fast ease-out hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-display text-lg font-semibold tracking-tight">
                    {trip.title ?? trip.destination.name}
                  </p>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                    {trip.status}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {trip.destination.name} · {trip.start_date} to {trip.end_date} ·{' '}
                  {trip.adults + trip.children} travelling
                </p>
                <p className="mt-2 text-sm font-medium">
                  <Money value={{ amount: trip.total_amount, currency: trip.currency }} />
                </p>
                {/* The reference, quietly. Nobody reads it until they email
                    support, and then it is the only thing that matters. */}
                <p className="mt-1 font-mono text-xs text-muted-foreground">{trip.reference}</p>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
