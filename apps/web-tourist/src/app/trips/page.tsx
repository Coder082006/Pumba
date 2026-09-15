'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import { ApiRequestError } from '@/lib/api';
import {
  EMPTY_SEGMENT,
  SEGMENT_LABEL,
  SEGMENTS,
  segmentOf,
  tripStatusLabel,
  type Segment,
} from '@/lib/trip-segments';
import { cancelTripBookings } from '@/lib/booking';
import { deleteTrip, listTrips, type TripSummary } from '@/lib/trips';

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
 *
 * §24.24 segments the list — Upcoming, Active, Past, Drafts — with an empty
 * state per segment and a draft's expiry shown. The segmenting rule is
 * `lib/trip-segments`, tested there; the page only draws it. It opens on the
 * first segment that has something in it, so a tourist with one draft does not
 * land on an empty "Upcoming".
 *
 * **A draft is thrown away; a reservation is cancelled.** Both tidy a row out
 * of Drafts, and they are not the same act, so the card offers whichever one
 * the trip's status permits. A plan (DRAFT or PRICED) is deleted outright. A
 * PENDING_PAYMENT trip has bookings against it, which §7.2 keeps, so it is
 * cancelled instead — the seats go back, the bookings become CANCELLED and the
 * trip moves to Past rather than vanishing. A delete button there would be the
 * server's 409 with extra steps.
 *
 * The confirmation is a second press of the same button rather than a
 * `window.confirm`. Neither act is reversible and one stray click should not
 * do either, but a modal for a plan nobody has paid for is heavier than the
 * act itself.
 */

/** Trip statuses the server will actually delete — `DISCARDABLE_STATES`. */
const DELETABLE = new Set(['DRAFT', 'PRICED']);

/** Reserved and not yet paid: cancellable, never deletable. */
const CANCELLABLE = new Set(['PENDING_PAYMENT']);

type State =
  | { status: 'loading' }
  | { status: 'ready'; trips: TripSummary[] }
  | { status: 'signed-out' }
  | { status: 'error'; message: string };

export default function MyTripsPage() {
  const [state, setState] = useState<State>({ status: 'loading' });
  const [chosen, setChosen] = useState<Segment | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const bySegment = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const groups: Record<Segment, TripSummary[]> = { UPCOMING: [], ACTIVE: [], PAST: [], DRAFTS: [] };
    if (state.status === 'ready') {
      for (const trip of state.trips) groups[segmentOf(trip, today)].push(trip);
    }
    return groups;
  }, [state]);

  const segment: Segment = chosen ?? SEGMENTS.find((key) => bySegment[key].length > 0) ?? 'UPCOMING';

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

  const remove = useCallback(async (trip: TripSummary) => {
    setRemoving(trip.public_id);
    setProblem(null);
    try {
      await deleteTrip(trip.public_id);
      // Dropped from the list here rather than by re-reading it: the row is
      // gone, and a refetch would blank the whole page to say so.
      setState((current) =>
        current.status === 'ready'
          ? { status: 'ready', trips: current.trips.filter((row) => row.public_id !== trip.public_id) }
          : current,
      );
      setConfirming(null);
    } catch (error) {
      setProblem(
        error instanceof ApiRequestError && error.status === 409
          ? 'That trip has bookings against it, so it is cancelled rather than deleted.'
          : 'That trip could not be deleted just now.',
      );
    } finally {
      setRemoving(null);
    }
  }, []);

  const cancel = useCallback(async (trip: TripSummary) => {
    setRemoving(trip.public_id);
    setProblem(null);
    try {
      await cancelTripBookings(trip.public_id);
      // Restated rather than removed, unlike a delete: the trip still exists,
      // in a state that files it under Past, and the row has to say so.
      setState((current) =>
        current.status === 'ready'
          ? {
              status: 'ready',
              trips: current.trips.map((row) =>
                row.public_id === trip.public_id ? { ...row, status: 'CANCELLED' } : row,
              ),
            }
          : current,
      );
      setConfirming(null);
    } catch (error) {
      setProblem(
        error instanceof ApiRequestError && error.status === 409
          ? 'Part of this trip has already started, so it can no longer be cancelled here.'
          : 'That reservation could not be cancelled just now.',
      );
    } finally {
      setRemoving(null);
    }
  }, []);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-bold tracking-tight">Your trips</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Every journey you have started planning.
          </p>
        </div>
        <Link
          href="/trips/new"
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90"
        >
          Plan a trip
        </Link>
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
            href="/trips/new"
            className="mt-4 inline-block rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90"
          >
            Plan a trip
          </Link>
          <p className="mt-3 text-sm text-muted-foreground">
            Not sure where yet?{' '}
            <Link href="/explore" className="text-primary hover:underline">
              Start exploring
            </Link>
            .
          </p>
        </div>
      ) : null}

      {state.status === 'ready' && state.trips.length > 0 ? (
        <div className="space-y-4">
          <div role="tablist" aria-label="Your trips" className="flex flex-wrap gap-2">
            {SEGMENTS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={segment === key}
                onClick={() => setChosen(key)}
                className={`rounded-full border px-4 py-1.5 text-sm transition-colors duration-fast ease-out ${
                  segment === key
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border hover:bg-muted'
                }`}
              >
                {SEGMENT_LABEL[key]} ({bySegment[key].length})
              </button>
            ))}
          </div>

          {problem ? (
            <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
              {problem}
            </p>
          ) : null}

          {bySegment[segment].length === 0 ? (
            <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              {EMPTY_SEGMENT[segment]}{' '}
              {segment === 'DRAFTS' || segment === 'UPCOMING' ? (
                <Link href="/trips/new" className="text-primary hover:underline">
                  Plan a trip
                </Link>
              ) : null}
            </p>
          ) : (
            <ul className="space-y-3" role="tabpanel">
              {bySegment[segment].map((trip) => (
                <li key={trip.public_id}>
                  <Link
                    href={
                      trip.status === 'CONFIRMED' || trip.status === 'IN_PROGRESS'
                        ? `/trips/${trip.public_id}/confirmation`
                        : `/trips/${trip.public_id}`
                    }
                    className="block rounded-lg border border-border p-4 transition-colors duration-fast ease-out hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="font-display text-lg font-semibold tracking-tight">
                        {trip.title ?? trip.destination.name}
                      </p>
                      <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        {tripStatusLabel(trip.status)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {trip.destination.name} · {trip.start_date} to {trip.end_date} ·{' '}
                      {trip.adults + trip.children} travelling
                    </p>
                    <p className="mt-2 text-sm font-medium">
                      <Money
                        value={{ amount: trip.total_amount, currency: trip.currency }}
                        display={trip.total_amount_display}
                      />
                    </p>
                    {segment === 'DRAFTS' && trip.quote_expires_at ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Price held until{' '}
                        <LocalTime value={trip.quote_expires_at} timeZone={trip.destination.timezone} />
                      </p>
                    ) : null}
                    {/* The reference, quietly. Nobody reads it until they email
                        support, and then it is the only thing that matters. */}
                    <p className="mt-1 font-mono text-xs text-muted-foreground">{trip.reference}</p>
                  </Link>
                  {DELETABLE.has(trip.status) || CANCELLABLE.has(trip.status) ? (
                    <p className="mt-1 flex justify-end gap-3 text-xs">
                      {confirming === trip.public_id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => setConfirming(null)}
                            className="text-muted-foreground hover:underline"
                          >
                            Keep it
                          </button>
                          <button
                            type="button"
                            disabled={removing === trip.public_id}
                            onClick={() =>
                              void (DELETABLE.has(trip.status) ? remove(trip) : cancel(trip))
                            }
                            className="font-semibold text-destructive-ink hover:underline disabled:opacity-60"
                          >
                            {removing === trip.public_id
                              ? 'Working…'
                              : DELETABLE.has(trip.status)
                                ? 'Delete for good'
                                : 'Yes, cancel it'}
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            setProblem(null);
                            setConfirming(trip.public_id);
                          }}
                          className="text-muted-foreground hover:text-destructive-ink hover:underline"
                        >
                          {DELETABLE.has(trip.status)
                            ? 'Delete this plan'
                            : 'Cancel this reservation'}
                        </button>
                      )}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
