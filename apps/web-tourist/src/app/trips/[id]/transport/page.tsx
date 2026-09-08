'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import type { ApiRequestError } from '@/lib/api';
import {
  isRoutingOutage,
  isUnconfigured,
  quoteTransfers,
  type LegQuote,
} from '@/lib/transport';
import { getTrip, type ItineraryItem, type Trip } from '@/lib/trips';

/**
 * Transportation — SRS §24.17.
 *
 *   Purpose: manage all non-airport transfer legs. Components: list of legs
 *   derived from the itinerary with origin, destination, time, distance,
 *   duration and price; per-leg vehicle-class selection; an add-custom-leg
 *   action with map pickers; a note where a leg was auto-inserted by the
 *   planner. States: legs with approximate estimates carry an explicit
 *   "approximate" badge (§12.6).
 *
 * **Every leg here was inserted by §10.4 rather than added by the tourist.**
 * That is what the "planned for you" note says, and it matters: a list that
 * looked hand-built would invite somebody to delete a leg the planner will
 * simply put back on the next generate. What a tourist *can* change is the
 * vehicle, which is what this screen is for.
 *
 * **The badge is on the distance, not on the price.** A corridor fare is a
 * fixed number an administrator set and is exact; the road length beside it is
 * a haversine estimate until Appendix D-2 is decided. §12.6 requires the
 * estimate to be labelled, and labelling the fare instead would be a different
 * and worse claim.
 *
 * The add-custom-leg action of §24.17 is not built here. It needs the map
 * picker of §13.2, and a half-built one that stored an unconfirmed coordinate
 * is exactly what §13.2 forbids — so it is left out rather than approximated.
 */
export default function TransportationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [trip, setTrip] = useState<Trip | null>(null);
  const [quotes, setQuotes] = useState<Record<string, LegQuote>>({});
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<ApiRequestError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTrip(id)
      .then(setTrip)
      .catch((error: unknown) => setFailure(error as ApiRequestError));
  }, [id]);

  const legs = useMemo(
    () => (trip?.itinerary?.items ?? []).filter((item) => item.item_type === 'TRANSFER'),
    [trip],
  );

  const party = trip ? trip.adults + trip.children : 0;

  const priceAll = useCallback(async () => {
    if (!trip || legs.length === 0) return;
    setBusy(true);
    setFailure(null);
    try {
      const answered = await quoteTransfers(
        trip.public_id,
        legs
          .filter((leg) => leg.origin_destination && leg.target_destination)
          .map((leg) => ({
            reference: leg.public_id,
            origin: { destination: leg.origin_destination!.slug },
            target: { destination: leg.target_destination!.slug },
            depart_at: leg.starts_at,
            pax: party,
            luggage: leg.luggage_count ?? party,
          })),
      );
      setQuotes(Object.fromEntries(answered.map((quote) => [quote.reference, quote])));
      setChosen(
        Object.fromEntries(
          answered.flatMap((quote) => {
            const leg = legs.find((candidate) => candidate.public_id === quote.reference);
            const preferred = leg?.vehicle_class ?? quote.options[0]?.vehicle_class;
            return preferred ? [[quote.reference, preferred]] : [];
          }),
        ),
      );
    } catch (error: unknown) {
      setFailure(error as ApiRequestError);
    } finally {
      setBusy(false);
    }
  }, [trip, legs, party]);

  if (!trip) {
    return <p className="mx-auto max-w-3xl px-4 py-10 text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
      <header>
        <h1 className="font-display text-2xl font-bold tracking-tight">Getting around</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {trip.reference} · {legs.length} {legs.length === 1 ? 'drive' : 'drives'} planned
        </p>
      </header>

      {legs.length === 0 ? (
        <section className="rounded-lg border border-dashed border-border p-4 text-sm">
          <p className="font-medium">No drives yet.</p>
          <p className="mt-1 text-muted-foreground">
            {/* A transfer only appears once there are two places to be between,
                so the honest next step is planning the days rather than adding
                a leg by hand. */}
            Plan your days and we will work out where you need to be driven.
          </p>
          <Link href={`/trips/${id}`} className="mt-3 inline-block font-medium underline">
            Back to the planner
          </Link>
        </section>
      ) : (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={() => void priceAll()}
            className="rounded-md border border-border px-4 py-2 text-sm font-semibold transition-colors duration-fast hover:bg-muted disabled:opacity-60"
          >
            {busy ? 'Checking…' : 'Show vehicle options'}
          </button>

          {failure ? <LegFailure error={failure} onRetry={() => void priceAll()} /> : null}

          <ul className="space-y-4">
            {legs.map((leg) => (
              <LegRow
                key={leg.public_id}
                leg={leg}
                timezone={trip.destination.timezone}
                quote={quotes[leg.public_id] ?? null}
                chosen={chosen[leg.public_id] ?? leg.vehicle_class ?? null}
                onChoose={(code) =>
                  setChosen((previous) => ({ ...previous, [leg.public_id]: code }))
                }
              />
            ))}
          </ul>
        </>
      )}

      <p className="text-sm">
        <Link href={`/trips/${id}`} className="underline">
          Back to the planner
        </Link>
      </p>
    </main>
  );
}

function LegRow({
  leg,
  timezone,
  quote,
  chosen,
  onChoose,
}: {
  leg: ItineraryItem;
  timezone: string;
  quote: LegQuote | null;
  chosen: string | null;
  onChoose: (code: string) => void;
}) {
  return (
    <li className="rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">{leg.title}</p>
        {leg.line_total && leg.currency ? (
          <Money
            value={{ amount: leg.line_total, currency: leg.currency }}
            display={leg.line_total_display}
            compact
            className="font-semibold"
          />
        ) : (
          <span className="text-sm text-muted-foreground">no fare yet</span>
        )}
      </div>

      <p className="mt-1 text-sm text-muted-foreground">
        Day {leg.day_number} ·{' '}
        <LocalTime value={leg.starts_at} timeZone={timezone} display="time" />
        {leg.travel_seconds ? `, about ${Math.round(leg.travel_seconds / 60)} minutes` : null}
        {leg.distance_m ? `, ${Math.round(leg.distance_m / 1000)} km` : null}
        {leg.is_approximate ? (
          // §12.6's "explicit label". On the distance, which is estimated —
          // never on the fare, which is a fixed corridor price somebody set.
          <span className="ml-2 rounded-full bg-warning/15 px-2 py-0.5 text-xs text-warning-foreground">
            approximate
          </span>
        ) : null}
      </p>

      {/* §24.17: "a note where a leg was auto-inserted by the planner". Every
          leg is, in v1, and saying so is what stops somebody hunting for the
          delete button. */}
      <p className="mt-1 text-xs text-muted-foreground">Planned for you around your days.</p>

      {quote ? (
        <fieldset className="mt-3">
          <legend className="text-sm font-medium">Vehicle</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {quote.options.map((option) => (
              <button
                key={option.vehicle_class}
                type="button"
                aria-pressed={option.vehicle_class === chosen}
                onClick={() => onChoose(option.vehicle_class)}
                className={`rounded-md border px-3 py-2 text-sm transition-colors duration-fast ${
                  option.vehicle_class === chosen
                    ? 'border-primary bg-primary/5 font-medium'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                {option.vehicle_class}
                <span className="ml-2 text-muted-foreground">
                  <Money value={option.price} display={option.price.display} compact />
                </span>
              </button>
            ))}
            {quote.options.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nothing we run fits this party — contact support.
              </p>
            ) : null}
          </div>
        </fieldset>
      ) : null}
    </li>
  );
}

function LegFailure({ error, onRetry }: { error: ApiRequestError; onRetry: () => void }) {
  if (isUnconfigured(error)) {
    return (
      <section className="rounded-lg border border-warning bg-warning/10 p-4 text-sm">
        <p className="font-medium">One of these routes is not priced yet.</p>
        <p className="mt-1 text-muted-foreground">
          We do not run a priced route between every pair of places. Contact support and we
          will arrange it.
        </p>
      </section>
    );
  }
  if (isRoutingOutage(error)) {
    return (
      <section className="rounded-lg border border-border p-4 text-sm">
        <p className="font-medium">We could not measure one of these routes.</p>
        <p className="mt-1 text-muted-foreground">
          Rather than guess at a fare, we would rather ask again in a moment.
        </p>
        <button type="button" onClick={onRetry} className="mt-3 font-medium underline">
          Try again
        </button>
      </section>
    );
  }
  return (
    <section className="rounded-lg border border-destructive bg-destructive/10 p-4 text-sm">
      <p className="font-medium">{error.message}</p>
    </section>
  );
}
