'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import type { ApiRequestError } from '@/lib/api';
import {
  isRoutingOutage,
  isUnconfigured,
  quoteTransfers,
  type FareOption,
  type LegQuote,
} from '@/lib/transport';
import { getTrip, type ItineraryItem, type Trip } from '@/lib/trips';

/**
 * Airport Pickup — SRS §24.16.
 *
 *   Purpose: book the arrival transfer. Components: origin (locked to the
 *   gateway), destination selector defaulting to the booked accommodation,
 *   computed pickup time with the buffer explained, pax and luggage,
 *   vehicle-class cards with capacity and price, meeting-point description with
 *   photograph, Add to Trip.
 *
 * **The origin is locked and the pickup time is computed**, which is the whole
 * shape of the screen: a tourist does not choose when to be collected from an
 * airport, the flight does. §24.15 captures the arrival and the pickup falls
 * `buffer.arrival_processing_minutes` after it — and §24.16 requires the buffer
 * to be *explained*, not merely applied, because forty-five minutes of
 * unexplained delay reads as the platform being slow rather than as
 * immigration being slow.
 *
 * **The two failure states are different sentences.** §24.16 spells them out:
 * `NO_TARIFF_CONFIGURED` renders as "transfers to this location are not yet
 * available — contact support", and a routing outage "shows a retry rather than
 * a guessed price". A screen that collapsed them would offer a retry that can
 * never succeed, which is worse than either message alone.
 *
 * **No price is computed here.** §18.1: "prices are computed server-side,
 * always. The client never sends a price; it sends selections." Every figure on
 * this page came from `POST /transport/quotes`.
 */
export default function AirportPickupPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [trip, setTrip] = useState<Trip | null>(null);
  const [quote, setQuote] = useState<LegQuote | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [luggage, setLuggage] = useState<number>(2);
  const [target, setTarget] = useState<string>('');
  const [failure, setFailure] = useState<ApiRequestError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getTrip(id)
      .then((loaded) => {
        setTrip(loaded);
        setLuggage(inboundLuggage(loaded) ?? loaded.adults + loaded.children);
        setTarget(defaultTarget(loaded) ?? '');
      })
      .catch((error: unknown) => setFailure(error as ApiRequestError));
  }, [id]);

  const inbound = useMemo(() => trip?.flights.find((f) => f.direction === 'INBOUND'), [trip]);
  const party = trip ? trip.adults + trip.children : 0;

  const price = useCallback(async () => {
    if (!trip || !inbound || !target) return;
    setBusy(true);
    setFailure(null);
    try {
      const [leg] = await quoteTransfers(trip.public_id, [
        {
          reference: 'airport-pickup',
          origin: { destination: inbound.gateway.slug },
          target: { accommodation: target },
          // The server times the pickup for the itinerary; for a quote what
          // matters is the *instant*, because §12.4 evaluates the night window
          // against it. The arrival is the honest one to send.
          depart_at: inbound.scheduled_at,
          pax: party,
          luggage,
        },
      ]);
      setQuote(leg ?? null);
      setChosen(leg?.options[0]?.vehicle_class ?? null);
    } catch (error: unknown) {
      setQuote(null);
      setFailure(error as ApiRequestError);
    } finally {
      setBusy(false);
    }
  }, [trip, inbound, target, party, luggage]);

  if (!trip) {
    return <p className="mx-auto max-w-3xl px-4 py-10 text-sm text-muted-foreground">Loading…</p>;
  }

  const stays = (trip.itinerary?.items ?? []).filter(
    (item) => item.item_type === 'STAY' && item.accommodation,
  );

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
      <header>
        <h1 className="font-display text-2xl font-bold tracking-tight">Your airport pickup</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {trip.reference} · {trip.destination.name}
        </p>
      </header>

      {!inbound ? (
        // §24.16 depends on §24.15 having happened. Saying which screen is
        // missing, and linking to it, is the difference between a dead end and
        // a next step.
        <section className="rounded-lg border border-dashed border-border p-4 text-sm">
          <p className="font-medium">We need your arrival first.</p>
          <p className="mt-1 text-muted-foreground">
            The pickup is timed from your flight, so tell us which one you are on.
          </p>
          <Link href={`/trips/${id}/flights`} className="mt-3 inline-block font-medium underline">
            Add your flight
          </Link>
        </section>
      ) : (
        <>
          <section className="space-y-4 rounded-lg border border-border p-4">
            <div>
              <p className="text-sm font-medium">Collected from</p>
              <p className="text-sm text-muted-foreground">
                {inbound.gateway.name} — {inbound.airline_iata}
                {inbound.flight_number}, landing{' '}
                <LocalTime value={inbound.scheduled_at} timeZone={trip.destination.timezone} display="datetime" />
              </p>
              {/* §24.16: "computed pickup time with the buffer explained". */}
              <p className="mt-1 text-xs text-muted-foreground">
                Your driver is scheduled after you clear immigration and collect
                your bags, not at the moment the wheels touch down.
              </p>
            </div>

            <label className="block text-sm">
              <span className="font-medium">Dropped at</span>
              <select
                value={target}
                onChange={(event) => {
                  setTarget(event.target.value);
                  setQuote(null);
                }}
                className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2"
              >
                <option value="">Choose where you are staying</option>
                {stays.map((stay) => (
                  <option key={stay.public_id} value={stay.accommodation?.slug ?? ''}>
                    {stay.accommodation?.name ?? stay.title}
                  </option>
                ))}
              </select>
              {stays.length === 0 ? (
                <span className="mt-1 block text-xs text-muted-foreground">
                  Add where you are staying to the trip first — the pickup has to go somewhere.
                </span>
              ) : null}
            </label>

            <div className="flex flex-wrap gap-4 text-sm">
              <p>
                <span className="font-medium">Travelling</span>
                <span className="ml-2 text-muted-foreground">
                  {party} {party === 1 ? 'person' : 'people'}
                </span>
              </p>
              <label>
                <span className="font-medium">Large bags</span>
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={luggage}
                  onChange={(event) => {
                    setLuggage(Number(event.target.value));
                    setQuote(null);
                  }}
                  className="ml-2 w-16 rounded-md border border-border bg-background px-2 py-1"
                />
              </label>
            </div>

            <button
              type="button"
              disabled={busy || !target}
              onClick={() => void price()}
              className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-60"
            >
              {busy ? 'Checking…' : 'See vehicles and prices'}
            </button>
          </section>

          {failure ? <PickupFailure error={failure} onRetry={() => void price()} /> : null}

          {quote ? (
            <VehicleCards
              quote={quote}
              chosen={chosen}
              onChoose={setChosen}
              party={party}
              luggage={luggage}
            />
          ) : null}
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

/**
 * §24.16's two states, and they are not interchangeable.
 *
 * An unconfigured route is a fact about our supply and will not change by
 * pressing a button; a routing outage is transient and a retry is exactly the
 * right thing to offer. Showing the wrong one wastes the tourist's time or
 * sends them to support over something that fixes itself.
 */
function PickupFailure({ error, onRetry }: { error: ApiRequestError; onRetry: () => void }) {
  if (isUnconfigured(error)) {
    return (
      <section className="rounded-lg border border-warning bg-warning/10 p-4 text-sm">
        <p className="font-medium">Transfers to this location are not yet available.</p>
        <p className="mt-1 text-muted-foreground">
          We do not run a priced route there yet. Contact support and we will arrange one.
        </p>
      </section>
    );
  }
  if (isRoutingOutage(error)) {
    return (
      <section className="rounded-lg border border-border p-4 text-sm">
        <p className="font-medium">We could not measure that route just now.</p>
        <p className="mt-1 text-muted-foreground">
          {/* §12.6: never a guessed price. Saying so is what makes the wait
              reasonable rather than mysterious. */}
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

/** §24.16: "vehicle-class cards with capacity and price". */
function VehicleCards({
  quote,
  chosen,
  onChoose,
  party,
  luggage,
}: {
  quote: LegQuote;
  chosen: string | null;
  onChoose: (code: string) => void;
  party: number;
  luggage: number;
}) {
  if (quote.options.length === 0) {
    return (
      <section className="rounded-lg border border-warning bg-warning/10 p-4 text-sm">
        <p className="font-medium">Nothing we run fits {party} travellers with {luggage} bags.</p>
        <p className="mt-1 text-muted-foreground">
          {/* A fact about the party, not about our configuration — the two look
              identical on screen and mean opposite things. */}
          Try splitting the party across two pickups, or contact support.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <h2 className="font-display text-lg font-semibold tracking-tight">Choose a vehicle</h2>
      <ul className="space-y-3">
        {quote.options.map((option) => (
          <VehicleCard
            key={option.vehicle_class}
            option={option}
            selected={option.vehicle_class === chosen}
            onChoose={() => onChoose(option.vehicle_class)}
          />
        ))}
      </ul>
      {quote.estimate_quality === 'APPROXIMATE' && quote.distance_m ? (
        // §12.6's explicit label, on the distance and never on the price: the
        // fare is a fixed corridor price and is exact, the road length is an
        // estimate. Badging the wrong one would be its own kind of lie.
        <p className="text-xs text-muted-foreground">
          About {Math.round(quote.distance_m / 1000)} km — distance approximate, price fixed.
        </p>
      ) : null}
    </section>
  );
}

function VehicleCard({
  option,
  selected,
  onChoose,
}: {
  option: FareOption;
  selected: boolean;
  onChoose: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onChoose}
        aria-pressed={selected}
        className={`flex w-full items-center justify-between gap-4 rounded-lg border p-4 text-left transition-colors duration-fast ${
          selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'
        }`}
      >
        <span>
          <span className="block font-medium">{option.vehicle_class}</span>
          <span className="block text-sm text-muted-foreground">
            Up to {option.seats} travellers · {option.luggage} large bags
          </span>
        </span>
        <span className="text-right font-semibold">
          <Money value={option.price} display={option.price.display} />
        </span>
      </button>
    </li>
  );
}

/** §24.15 captures the luggage per direction; the pickup is quoted for it. */
function inboundLuggage(trip: Trip): number | null {
  const inbound = trip.flights.find((flight) => flight.direction === 'INBOUND');
  return inbound?.luggage_count ?? null;
}

/** §24.16: "destination selector defaulting to the booked accommodation". */
function defaultTarget(trip: Trip): string | null {
  const stay = (trip.itinerary?.items ?? []).find(
    (item: ItineraryItem) => item.item_type === 'STAY' && item.accommodation,
  );
  return stay?.accommodation?.slug ?? null;
}
