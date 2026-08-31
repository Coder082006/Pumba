'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { ApiRequestError } from '@/lib/api';
import { addItem, getTrip, listTrips, type AddItemInput, type TripSummary } from '@/lib/trips';

/**
 * Add a catalogue row to a trip — SRS §24.9, §24.10, §24.11, §41.3.
 *
 * One component for all three catalogue screens. The activity, attraction and
 * stay pages each knew their own subject long before there was a trip to put it
 * in; they carried a disabled "Add to trip — coming soon" button because
 * `apps/api/apps/trip/` was a Phase 1 skeleton. It is not any more, and this is
 * the wire between them.
 *
 * **Only DRAFT trips are offered.** `domain.lifecycle.EDITABLE_STATES` is DRAFT
 * alone: everything past it has money or inventory committed, and the API
 * answers an edit with 409. Offering a trip that cannot accept the item would
 * be inviting a refusal.
 *
 * **`sequence_no` is "one past the day's current maximum", and it does not
 * matter.** §10.4 line 20 renumbers every item in a day from 1 on the next
 * generate, so this is a temporary position that keeps the row unique within
 * `(itinerary, day, sequence)`. Anything cleverer would be guessing at an order
 * the planner is about to decide.
 *
 * **No title is sent.** The server takes it from the listing, so two tourists
 * adding the same activity get the same words — and a title invented here would
 * end up in an emailed confirmation.
 *
 * **`timing` is data, never a callback.** Two of the three callers are Server
 * Components, and a function prop cannot cross that boundary. It also keeps the
 * shape of an item's times in the page that knows them: an activity has a
 * duration, an attraction a recommended visit, a stay the nights the tourist
 * picked.
 */

/** Where an item sits in time, in the two shapes the catalogue produces. */
export type Timing =
  /**
   * The tourist picks a day and a start time; the listing supplies the length.
   * Activities and attractions.
   */
  | { kind: 'on-day'; durationMinutes: number; defaultStart: string }
  /**
   * The dates are already chosen — the stay picker's own check-in and check-out
   * — so the day number is derived rather than asked for again.
   */
  | { kind: 'dates'; startDate: string; endDate: string; startTime: string; endTime: string };

export interface AddToTripProps {
  /** What the API needs, minus the placement this control works out. */
  item: Omit<AddItemInput, 'day_number' | 'sequence_no' | 'starts_at' | 'ends_at'>;
  timing: Timing;
  label?: string;
}

type Status =
  | { kind: 'loading' }
  | { kind: 'signed-out' }
  | { kind: 'none' }
  | { kind: 'ready'; trips: TripSummary[] }
  | { kind: 'added'; tripId: string }
  | { kind: 'error'; message: string };

const MS_PER_DAY = 86_400_000;

/** Whole days between two `YYYY-MM-DD` dates, midnight to midnight in UTC. */
export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / MS_PER_DAY);
}

function dayCount(trip: TripSummary): number {
  return Math.max(1, daysBetween(trip.start_date, trip.end_date) + 1);
}

function dateOnDay(trip: TripSummary, dayNumber: number): string {
  const at = new Date(Date.parse(`${trip.start_date}T00:00:00Z`) + (dayNumber - 1) * MS_PER_DAY);
  return at.toISOString().slice(0, 10);
}

/**
 * How far the zone is ahead of UTC at a given instant, in milliseconds.
 *
 * `Intl` will render an instant in any IANA zone but will not parse one, so the
 * offset is measured by rendering and comparing.
 */
function zoneOffsetMs(at: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(at);
  const field = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  const rendered = Date.UTC(
    field('year'),
    field('month') - 1,
    field('day'),
    field('hour') % 24, // Some locales render midnight as 24.
    field('minute'),
    field('second'),
  );
  return rendered - at.getTime();
}

/**
 * A local wall time at the destination, as the ISO instant the API stores.
 *
 * **Not the browser's zone**, and the distinction is the whole point. A tourist
 * plans from home: 18:00 in Zanzibar entered from London is 15:00 UTC, and
 * reading it as 18:00 UTC files the item on the wrong local day for anything
 * late in the evening. The same mistake, made server-side with
 * `timezone.localtime()`, is what put every generated day number one out
 * earlier in this phase.
 *
 * Two passes: the first offset is measured at the guessed instant, which can be
 * on the wrong side of a DST change; measuring again at the corrected instant
 * settles it.
 */
export function instantAt(date: string, time: string, timeZone: string): string {
  const guess = new Date(`${date}T${time}:00Z`);
  const once = new Date(guess.getTime() - zoneOffsetMs(guess, timeZone));
  return new Date(guess.getTime() - zoneOffsetMs(once, timeZone)).toISOString();
}

function zoneOf(trip: TripSummary): string {
  // The API always sends it; the schema does not mark it required. The
  // browser's own zone is a poor answer but a visible one — the times land
  // where a person planning at home would read them — whereas defaulting to UTC
  // would look right and be silently wrong for every destination.
  return trip.destination.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function AddToTrip({ item, timing, label = 'Add to trip' }: AddToTripProps) {
  const [status, setStatus] = useState<Status>({ kind: 'loading' });
  const [tripId, setTripId] = useState('');
  const [day, setDay] = useState('1');
  const [start, setStart] = useState(timing.kind === 'on-day' ? timing.defaultStart : '');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const drafts = (await listTrips()).filter((trip) => trip.status === 'DRAFT');
        if (drafts.length === 0) {
          setStatus({ kind: 'none' });
          return;
        }
        setTripId(drafts[0]!.public_id);
        setStatus({ kind: 'ready', trips: drafts });
      } catch (error) {
        setStatus(
          error instanceof ApiRequestError && error.status === 401
            ? { kind: 'signed-out' }
            : { kind: 'error', message: 'Your trips could not be loaded just now.' },
        );
      }
    })();
  }, []);

  const add = useCallback(async () => {
    if (status.kind !== 'ready') return;
    const trip = status.trips.find((candidate) => candidate.public_id === tripId);
    if (!trip) return;

    const zone = zoneOf(trip);
    let dayNumber: number;
    let startsAt: string;
    let endsAt: string;

    if (timing.kind === 'dates') {
      dayNumber = daysBetween(trip.start_date, timing.startDate) + 1;
      if (dayNumber < 1 || dayNumber > dayCount(trip)) {
        setStatus({
          kind: 'error',
          message: `Those dates are outside this trip, which runs ${trip.start_date} to ${trip.end_date}.`,
        });
        return;
      }
      startsAt = instantAt(timing.startDate, timing.startTime, zone);
      endsAt = instantAt(timing.endDate, timing.endTime, zone);
    } else {
      dayNumber = Number(day);
      startsAt = instantAt(dateOnDay(trip, dayNumber), start, zone);
      endsAt = new Date(Date.parse(startsAt) + timing.durationMinutes * 60_000).toISOString();
    }

    setBusy(true);
    try {
      // The next free position on that day. Read fresh rather than remembered:
      // another tab may have added something since this page loaded.
      const current = await getTrip(tripId);
      const onDay = (current.itinerary?.items ?? []).filter((row) => row.day_number === dayNumber);
      const nextPosition = Math.max(0, ...onDay.map((row) => row.sequence_no)) + 1;

      await addItem(tripId, {
        ...item,
        day_number: dayNumber,
        sequence_no: nextPosition,
        starts_at: startsAt,
        ends_at: endsAt,
      });
      setStatus({ kind: 'added', tripId });
    } catch (error) {
      setStatus({
        kind: 'error',
        message:
          error instanceof ApiRequestError
            ? error.message
            : 'That could not be added to your trip.',
      });
    } finally {
      setBusy(false);
    }
  }, [status, tripId, day, start, item, timing]);

  if (status.kind === 'loading') {
    return <div aria-hidden className="h-10 rounded-md bg-muted" />;
  }

  if (status.kind === 'signed-out') {
    return (
      <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
        <Link href="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>{' '}
        to add this to a trip.
      </p>
    );
  }

  if (status.kind === 'none') {
    return (
      <Link
        href="/trips/new"
        className="block rounded-md bg-primary px-4 py-2 text-center text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90"
      >
        Plan a trip first
      </Link>
    );
  }

  if (status.kind === 'added') {
    return (
      <p className="rounded-md border border-border bg-accent/10 px-3 py-2 text-sm">
        Added.{' '}
        <Link href={`/trips/${status.tripId}`} className="font-medium text-primary hover:underline">
          Open the planner
        </Link>{' '}
        to plan the days around it.
      </p>
    );
  }

  if (status.kind === 'error') {
    return (
      <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive-ink">
        {status.message}
      </p>
    );
  }

  const chosen = status.trips.find((candidate) => candidate.public_id === tripId);
  const field = 'mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm';

  return (
    <div className="space-y-3">
      <label className="block text-sm">
        Add to
        <select
          className={field}
          value={tripId}
          onChange={(event) => setTripId(event.target.value)}
        >
          {status.trips.map((trip) => (
            <option key={trip.public_id} value={trip.public_id}>
              {trip.title ?? trip.destination.name} · {trip.start_date}
            </option>
          ))}
        </select>
      </label>

      {timing.kind === 'on-day' ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            Which day
            <select className={field} value={day} onChange={(event) => setDay(event.target.value)}>
              {Array.from({ length: chosen ? dayCount(chosen) : 1 }, (_, index) => index + 1).map(
                (number) => (
                  <option key={number} value={number}>
                    Day {number}
                    {chosen ? ` · ${dateOnDay(chosen, number)}` : ''}
                  </option>
                ),
              )}
            </select>
          </label>
          <label className="text-sm">
            Starting at
            <input
              type="time"
              className={field}
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
          </label>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {timing.startDate} to {timing.endDate}, from the dates you chose above.
        </p>
      )}

      <button
        type="button"
        disabled={busy}
        onClick={() => void add()}
        className="w-full rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60"
      >
        {busy ? 'Adding…' : label}
      </button>

      <p className="text-xs text-muted-foreground">
        Times are local to {chosen?.destination.name ?? 'the destination'}, and the planner will
        adjust them when you generate the itinerary.
      </p>
    </div>
  );
}
