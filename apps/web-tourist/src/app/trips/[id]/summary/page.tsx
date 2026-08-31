'use client';

import Link from 'next/link';
import { use, useEffect, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import { FindingList, ValidationBanner } from '@/components/trip/findings';
import { ApiRequestError } from '@/lib/api';
import { byDay, type ItineraryItem, type Trip, getTrip } from '@/lib/trips';

/**
 * Trip Summary and Cost Breakdown — SRS §24.20, §24.21.
 *
 * §24.20 calls this *"the final review before payment — the screen the entire
 * product exists to produce"*, and §24.21 asks for *"complete transparency on
 * what is being charged"*. Both are built here rather than as two routes: the
 * breakdown takes its data from the trip payload with no separate call
 * (§24.21), so splitting them would mean fetching the same trip twice to show
 * two halves of one answer.
 *
 * **Continue to Payment is disabled, and says why.** §24.20's action leads to
 * `POST /trips/{id}/quote`, which converts a plan into an inventory-backed,
 * time-boxed offer (§9.4.5) — that is the booking engine, Phase 7. A button
 * that looked live and 404ed would be worse than one that explains itself.
 *
 * **Blocking errors disable Continue and link to the offending item** (§24.20).
 * `has_errors` is the server's, computed once in §10.6; a client that counted
 * ERROR findings itself would be a second implementation of the quote gate.
 *
 * **Two figures §24.21 asks for are absent, and are named rather than faked.**
 * Attraction entrance fees — §15.3 makes them payable on site and explicitly
 * excluded from any subtotal, and §24.21 wants them in *"a separate clearly
 * labelled block"* — are not on the item payload, so the block says the
 * platform does not yet collect them rather than showing a total of zero,
 * which would read as "free". And a converted currency's source and rate
 * (§18.4) has nothing behind it until FX exists.
 *
 * Commission is never shown here. §18.3: it is deducted from the provider's
 * share and is invisible to the tourist, and the platform service fee is a
 * separate, tourist-facing line.
 */

const SERVICE_LABEL: Record<string, string> = {
  ACTIVITY: 'Activities',
  TRANSFER: 'Transfers',
  STAY: 'Stays',
  ATTRACTION: 'Attractions',
  FREE_TIME: 'Free time',
};

function groupByService(items: readonly ItineraryItem[]): Map<string, ItineraryItem[]> {
  const groups = new Map<string, ItineraryItem[]>();
  for (const item of items) {
    if (!item.line_total) continue;
    const group = groups.get(item.item_type) ?? [];
    group.push(item);
    groups.set(item.item_type, group);
  }
  return groups;
}

export default function TripSummaryPage({ params }: { params: Promise<{ id: string }> }) {
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
            : 'This trip could not be loaded just now.',
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
  const currency = trip.currency;
  const groups = groupByService(itinerary?.items ?? []);
  const blocked = itinerary?.has_errors ?? false;
  const hasAttractions = (itinerary?.items ?? []).some((i) => i.item_type === 'ATTRACTION');

  return (
    <div className="space-y-10">
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
          {trip.destination.name} · {trip.start_date} to {trip.end_date} ·{' '}
          {trip.adults + trip.children} travelling · {trip.reference}
        </p>
      </header>

      <ValidationBanner hasErrors={blocked} generated={Boolean(itinerary?.generated_at)} />
      <FindingList findings={itinerary?.findings ?? []} />

      {trip.flights.length > 0 ? (
        <section aria-labelledby="arrival" className="rounded-lg border border-border p-4">
          <h2 id="arrival" className="font-display text-lg font-semibold tracking-tight">
            Arrival
          </h2>
          {trip.flights
            .filter((f) => f.direction === 'INBOUND')
            .map((f) => (
              <p key={f.direction} className="mt-2 text-sm text-muted-foreground">
                {f.airline_iata}
                {f.flight_number} into {f.gateway.name},{' '}
                <LocalTime value={f.scheduled_at} timeZone={zone} display="datetime" />
              </p>
            ))}
        </section>
      ) : null}

      <section aria-labelledby="days">
        <h2 id="days" className="font-display text-xl font-semibold tracking-tight">
          Day by day
        </h2>
        <ul className="mt-3 space-y-2 text-sm">
          {[...byDay(itinerary?.items ?? []).entries()].map(([day, items]) => (
            <li key={day} className="flex gap-3 border-b border-border pb-2">
              <span className="w-16 shrink-0 font-medium">Day {day}</span>
              <span className="text-muted-foreground">
                {items
                  .filter((i) => i.item_type !== 'TRANSFER')
                  .map((i) => i.title)
                  .join(' · ') || 'Nothing planned'}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="cost">
        <h2 id="cost" className="font-display text-xl font-semibold tracking-tight">
          What this costs
        </h2>

        {groups.size === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            Nothing priced yet. Add activities and plan the days.
          </p>
        ) : (
          <div className="mt-3 space-y-6">
            {[...groups.entries()].map(([type, items]) => (
              <div key={type}>
                <h3 className="text-sm font-semibold">{SERVICE_LABEL[type] ?? type}</h3>
                <ul className="mt-2 space-y-1 text-sm">
                  {items.map((item) => (
                    <li key={item.public_id} className="flex justify-between gap-4">
                      <span className="text-muted-foreground">
                        {item.title}
                        {item.quantity > 1 ? ` × ${item.quantity}` : null}
                      </span>
                      <Money
                        value={{ amount: item.line_total ?? '0', currency: item.currency ?? currency }}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}

        <dl className="mt-6 space-y-2 border-t border-border pt-4 text-sm">
          <div className="flex justify-between">
            <dt className="text-muted-foreground">Subtotal</dt>
            <dd>
              <Money value={{ amount: trip.subtotal_amount, currency }} />
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">
              Service fee
              <span className="block text-xs">
                What we charge for planning and running the trip.
              </span>
            </dt>
            <dd>
              <Money value={{ amount: trip.fee_amount, currency }} />
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">Taxes</dt>
            <dd>
              <Money value={{ amount: trip.tax_amount, currency }} />
            </dd>
          </div>
          <div className="flex justify-between border-t border-border pt-2 text-base font-semibold">
            <dt>Total</dt>
            <dd>
              <Money value={{ amount: trip.total_amount, currency }} />
            </dd>
          </div>
        </dl>

        <p className="mt-3 text-xs text-muted-foreground">
          Transfers are timed but not yet priced — fares arrive with the transport module. Nothing
          here is charged until you pay.
        </p>

        {hasAttractions ? (
          <div className="mt-6 rounded-lg border border-dashed border-border p-4 text-sm">
            <h3 className="font-semibold">Paid on the day, not by us</h3>
            <p className="mt-1 text-muted-foreground">
              Some attractions charge entry at the gate. We do not collect those, and we do not
              show an amount for them yet — so budget separately rather than reading the total
              above as everything you will spend.
            </p>
          </div>
        ) : null}
      </section>

      <section className="border-t border-border pt-6">
        <button
          type="button"
          disabled
          className="rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground opacity-60"
        >
          Continue to payment
        </button>
        <p className="mt-2 text-sm text-muted-foreground">
          {blocked
            ? 'Fix the errors above first — they are listed against the items they affect.'
            : 'Booking and payment are not built yet. This trip is planned and priced; nothing can be reserved until the booking engine lands.'}
        </p>
      </section>
    </div>
  );
}
