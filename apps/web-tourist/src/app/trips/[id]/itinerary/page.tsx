'use client';

import Link from 'next/link';
import { use, useEffect, useState } from 'react';
import { LocalTime } from '@pumba/ui';

import { DayTimeline } from '@/components/trip/day-timeline';
import { FindingList } from '@/components/trip/findings';
import { ApiRequestError } from '@/lib/api';
import { getTrip, tripLevelFindings, type Trip } from '@/lib/trips';

/**
 * Itinerary — SRS §24.19, *"the day-by-day journey view"*.
 *
 * The read-only sibling of the planner. Same timeline component, no remove
 * buttons: this is the screen somebody opens on the morning of day three, and
 * a delete control beside every row is a hazard rather than a convenience.
 *
 * §24.19 also asks for download-PDF, share, and an offline badge when served
 * from cache. **None of those is built, and stubbing them would be worse than
 * their absence** — a share button that does nothing, or an offline badge over
 * a page that is not cached, is a promise the product does not keep. They need
 * a service worker and a document renderer, neither of which exists; recorded
 * here rather than mocked.
 *
 * Flights are shown because §24.19's connectors only make sense with the
 * arrival they hang from: a transfer at 14:45 is unexplained until you can see
 * the flight landing at 14:00.
 */

export default function ItineraryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setTrip(await getTrip(id));
      } catch (error) {
        setProblem(
          error instanceof ApiRequestError && error.status === 404
            ? 'That trip could not be found.'
            : 'This itinerary could not be loaded just now.',
        );
      }
    })();
  }, [id]);

  if (problem) {
    return (
      <div className="rounded-lg border border-border p-8 text-center">
        <p className="text-muted-foreground">{problem}</p>
        <Link href="/trips" className="mt-4 inline-block text-sm font-medium text-primary">
          Back to your trips
        </Link>
      </div>
    );
  }
  if (!trip) return <div aria-hidden className="h-64 rounded-lg bg-muted" />;

  const itinerary = trip.itinerary;
  const zone = trip.destination.timezone;

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <Link href={`/trips/${id}`} className="hover:underline">
            Back to the planner
          </Link>
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">
          {trip.title ?? trip.destination.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          {trip.start_date} to {trip.end_date} · all times shown in{' '}
          {trip.destination.name}&rsquo;s local time
        </p>
      </header>

      {trip.flights.length > 0 ? (
        <section aria-labelledby="flights" className="rounded-lg border border-border p-4">
          <h2 id="flights" className="font-display text-lg font-semibold tracking-tight">
            Flights
          </h2>
          <ul className="mt-3 space-y-2 text-sm">
            {trip.flights.map((flight) => (
              <li key={flight.direction} className="flex flex-wrap gap-x-3 text-muted-foreground">
                <span className="font-medium text-foreground">
                  {flight.airline_iata}
                  {flight.flight_number}
                </span>
                <span>{flight.direction === 'INBOUND' ? 'arriving' : 'leaving'}</span>
                <span>{flight.gateway.name}</span>
                <LocalTime value={flight.scheduled_at} timeZone={zone} display="datetime" />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {itinerary?.generated_at ? (
        <p className="text-sm text-muted-foreground">
          Planned <LocalTime value={itinerary.generated_at} timeZone={zone} display="datetime" />.
        </p>
      ) : (
        <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
          This trip has not been planned yet. Open the planner and choose{' '}
          <span className="font-medium">Plan the days</span>.
        </p>
      )}

      <FindingList findings={tripLevelFindings(itinerary?.findings ?? [])} />

      <DayTimeline
        items={itinerary?.items ?? []}
        findings={itinerary?.findings ?? []}
        timezone={zone}
      />
    </div>
  );
}
