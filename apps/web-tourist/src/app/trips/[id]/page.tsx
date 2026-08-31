'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useState } from 'react';
import { Money } from '@pumba/ui';

import { DayTimeline } from '@/components/trip/day-timeline';
import { FindingList, ValidationBanner } from '@/components/trip/findings';
import { ApiRequestError } from '@/lib/api';
import {
  generateItinerary,
  getTrip,
  removeItem,
  tripLevelFindings,
  type ItineraryItem,
  type Trip,
} from '@/lib/trips';

/**
 * Trip Planner — SRS §24.14, *"the workspace where the journey is assembled"*.
 *
 * Three behaviours the specification asks for, and the reasons they are shaped
 * this way rather than the obvious way.
 *
 * **An error keeps the last good itinerary on screen.** §24.14 says so
 * explicitly, and it is the difference between a failed regenerate that costs
 * the reader a retry and one that costs them their plan. So a failure sets an
 * error beside the trip rather than replacing it.
 *
 * **Findings render against their items**, not as a summary. §10.6 carries
 * `item_ids` for exactly that, and only findings that name nothing — VR-16 is
 * about nights with no stay — appear as a banner.
 *
 * **The running total is the server's.** §10.7 computes it with `Decimal` and
 * ROUND_HALF_UP applied once per line and once per aggregate, and §7.5.10 has a
 * database CHECK that the total equals its parts. A client that summed the
 * lines itself would be a second implementation of the pricing path, in
 * floating point, disagreeing in the last cent.
 *
 * What is deliberately absent: drag-to-reorder and the add-item action sheet.
 * §10.4 assigns `sequence_no` itself and rewrites it on every generate, so a
 * dragged order would survive until the next plan and no longer; adding items
 * belongs to the catalogue screens, which already know what an activity is.
 * Both are recorded here rather than half-built.
 */

type State =
  | { status: 'loading' }
  | { status: 'ready'; trip: Trip }
  | { status: 'signed-out' }
  | { status: 'missing' }
  | { status: 'error'; message: string };

export default function TripPlannerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [state, setState] = useState<State>({ status: 'loading' });
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ status: 'loading' });
    try {
      setState({ status: 'ready', trip: await getTrip(id) });
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 401) {
        setState({ status: 'signed-out' });
      } else if (error instanceof ApiRequestError && error.status === 404) {
        // §30.3: a trip that is not yours and a trip that does not exist are
        // the same answer, and this screen must not distinguish them either.
        setState({ status: 'missing' });
      } else {
        setState({ status: 'error', message: 'This trip could not be loaded just now.' });
      }
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Runs `action`, keeping the current trip on screen if it fails (§24.14). */
  const mutate = useCallback(
    async (action: () => Promise<Trip>) => {
      setBusy(true);
      setProblem(null);
      try {
        setState({ status: 'ready', trip: await action() });
      } catch (error) {
        setProblem(
          error instanceof ApiRequestError
            ? error.message
            : 'That change could not be saved just now.',
        );
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  if (state.status === 'loading') {
    return <div aria-hidden className="h-64 rounded-lg bg-muted" />;
  }
  if (state.status === 'signed-out') {
    return (
      <p className="rounded-lg border border-border bg-muted p-6 text-sm">
        <Link href="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>{' '}
        to open this trip.
      </p>
    );
  }
  if (state.status === 'missing') {
    return (
      <div className="rounded-lg border border-border p-8 text-center">
        <p className="text-muted-foreground">That trip could not be found.</p>
        <Link href="/trips" className="mt-4 inline-block text-sm font-medium text-primary">
          Back to your trips
        </Link>
      </div>
    );
  }
  if (state.status === 'error') {
    return (
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
    );
  }

  const { trip } = state;
  const itinerary = trip.itinerary;
  const findings = itinerary?.findings ?? [];
  // The destination's zone, carried on the ref. Formatting in the browser's
  // zone would undo the day-boundary work the server does in the trip's.
  const timezone = trip.destination.timezone;

  return (
    <div className="space-y-8 pb-24">
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <Link href="/trips" className="hover:underline">
            Your trips
          </Link>
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">
          {trip.title ?? trip.destination.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          {trip.destination.name} · {trip.start_date} to {trip.end_date} ·{' '}
          {trip.adults + trip.children} travelling · {trip.status}
        </p>
        <nav className="flex flex-wrap gap-3 pt-2 text-sm">
          <Link href={`/trips/${id}/flights`} className="font-medium text-primary hover:underline">
            Flights
          </Link>
          <Link
            href={`/trips/${id}/itinerary`}
            className="font-medium text-primary hover:underline"
          >
            Itinerary
          </Link>
          <Link href={`/trips/${id}/summary`} className="font-medium text-primary hover:underline">
            Summary
          </Link>
        </nav>
      </header>

      <ValidationBanner
        hasErrors={itinerary?.has_errors ?? false}
        generated={Boolean(itinerary?.generated_at)}
      />

      <FindingList findings={tripLevelFindings(findings)} />

      {problem ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive-ink">
          {problem}
        </p>
      ) : null}

      <DayTimeline
        items={itinerary?.items ?? []}
        findings={findings}
        timezone={timezone}
        onRemove={(item: ItineraryItem) =>
          void mutate(() => removeItem(id, item.public_id))
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => void mutate(() => generateItinerary(id))}
          className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {busy ? 'Planning…' : 'Plan the days'}
        </button>
        <Link
          href="/explore"
          className="rounded-md border border-border px-6 py-3 text-sm font-semibold transition-colors duration-fast ease-out hover:bg-muted"
        >
          Add something to do
        </Link>
      </div>

      {/* §24.14's "running total footer". The figures are the server's; see the
          module docstring for why none of them is computed here. */}
      <footer className="fixed inset-x-0 bottom-0 border-t border-border bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <p className="text-sm text-muted-foreground">
            Estimated so far ·{' '}
            <span className="font-medium text-foreground">
              <Money value={{ amount: trip.total_amount, currency: trip.currency }} />
            </span>
          </p>
          <Link
            href={`/trips/${id}/summary`}
            className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground"
          >
            Continue
          </Link>
        </div>
      </footer>
    </div>
  );
}
