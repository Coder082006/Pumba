'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { use, useCallback, useEffect, useRef, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import { ApiRequestError } from '@/lib/api';
import { confirmBasket } from '@/lib/booking';
import { getTrip, quoteTrip, type Quote, type Trip } from '@/lib/trips';

/**
 * Checkout — SRS §24.20's action, §9.4.5 and §9.4.6.
 *
 * Two server calls, in the order the specification gives them. Opening the
 * page **quotes** the trip: seats are held under a row lock and a fresh token
 * is issued, so the total on screen is one the platform has committed to, not
 * a figure from whenever the planner last ran. Pressing the button **confirms**
 * the basket with that token: one booking per component, the seats held for
 * the payment window.
 *
 * **No money moves, and the page says so in the button's own words.** Payment
 * capture is Phase 8. A tourist who reads "Reserve" and then "nothing has been
 * charged yet" knows exactly where they stand; one who read "Pay" would not.
 *
 * Every refusal the basket can return is shown as what it means to the
 * tourist, not as a code: an expired offer invites a new price, an operator
 * that cannot sell right now names the item, a moved fare says the price
 * changed.
 */
export default function CheckoutPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [trip, setTrip] = useState<Trip | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [details, setDetails] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  // One idempotency key per attempt at this quote, so a retry after a timeout
  // gets the basket it already made rather than a conflict.
  const attempt = useRef<string>(crypto.randomUUID());

  const price = useCallback(async () => {
    setProblem(null);
    setDetails([]);
    try {
      const fresh = await quoteTrip(id);
      setQuote(fresh);
      setTrip(await getTrip(id));
      attempt.current = crypto.randomUUID();
    } catch (error) {
      setProblem(explain(error));
      setDetails(detailLines(error));
    }
  }, [id]);

  useEffect(() => {
    void price();
  }, [price]);

  const reserve = useCallback(async () => {
    if (!quote) return;
    setBusy(true);
    setProblem(null);
    setDetails([]);
    try {
      await confirmBasket(id, quote.quote_token, attempt.current);
      router.push(`/trips/${id}/confirmation`);
    } catch (error) {
      setProblem(explain(error));
      setDetails(detailLines(error));
    } finally {
      setBusy(false);
    }
  }, [id, quote, router]);

  const zone = trip?.destination.timezone;

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <Link href={`/trips/${id}/summary`} className="hover:underline">
            Back to the summary
          </Link>
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">Checkout</h1>
        {trip ? (
          <p className="text-sm text-muted-foreground">
            {trip.title ?? trip.destination.name} · {trip.start_date} to {trip.end_date}
          </p>
        ) : null}
      </header>

      {problem ? (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <p className="font-medium">{problem}</p>
          {details.length > 0 ? (
            <ul className="mt-2 list-disc pl-5">
              {details.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            onClick={() => void price()}
            className="mt-3 font-medium text-primary hover:underline"
          >
            Get a new price
          </button>
        </div>
      ) : null}

      {!quote && !problem ? <div aria-hidden className="h-40 rounded-lg bg-muted" /> : null}

      {quote && trip ? (
        <section className="space-y-4 rounded-lg border border-border p-6">
          <div className="flex items-baseline justify-between">
            <span className="text-muted-foreground">Total</span>
            <span className="text-2xl font-semibold">
              <Money
                value={{ amount: quote.total_amount, currency: quote.currency }}
                display={trip.total_amount_display}
              />
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            {quote.held_seats > 0 ? `${quote.held_seats} places held` : 'Nothing needed holding'} until{' '}
            {zone ? <LocalTime value={quote.expires_at} timeZone={zone} /> : quote.expires_at}.
          </p>
          <ul className="divide-y divide-border text-sm">
            {(trip.itinerary?.items ?? [])
              .filter((item) => item.item_type === 'ACTIVITY' || item.item_type === 'TRANSFER')
              .map((item) => (
                <li key={item.public_id} className="flex justify-between gap-4 py-2">
                  <span>{item.title}</span>
                  <Money
                    value={{ amount: item.line_total ?? '0', currency: item.currency ?? quote.currency }}
                    display={item.line_total_display}
                    compact
                  />
                </li>
              ))}
          </ul>
          <button
            type="button"
            disabled={busy}
            onClick={() => void reserve()}
            className="w-full rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {busy ? 'Reserving…' : 'Reserve these bookings'}
          </button>
          <p className="text-xs text-muted-foreground">
            Reserving creates your bookings and holds your places while you pay. Payment arrives in
            the next release, so nothing has been charged yet. Each booking keeps the cancellation
            policy it is sold under today.
          </p>
        </section>
      ) : null}
    </div>
  );
}

function explain(error: unknown): string {
  if (!(error instanceof ApiRequestError)) return 'Checkout could not be reached just now.';
  switch (error.code) {
    case 'QUOTE_EXPIRED':
    case 'HOLD_EXPIRED':
      return 'Your held places have lapsed. Get a new price to hold them again.';
    case 'PRICE_CHANGED':
      return 'A fare has changed since you were quoted.';
    case 'NOT_BOOKABLE':
      return 'Part of this trip cannot be booked right now:';
    case 'INVENTORY_UNAVAILABLE':
      return 'Something on this trip is no longer available:';
    case 'TRIP_NOT_QUOTABLE':
      return 'This trip needs planning before it can be priced. Go back to the planner.';
    case 'TRIP_NOT_PAYABLE':
      return 'This trip has already been through checkout.';
    default:
      return error.message;
  }
}

function detailLines(error: unknown): string[] {
  if (!(error instanceof ApiRequestError)) return [];
  return error.details
    .map((detail) => {
      const record = detail as Record<string, unknown>;
      const title = typeof record.title === 'string' ? record.title : null;
      const reason = typeof record.reason === 'string' ? record.reason : null;
      if (title && reason) return `${title} — ${readable(reason)}`;
      return title ?? reason ?? null;
    })
    .filter((line): line is string => line !== null);
}

function readable(reason: string): string {
  switch (reason) {
    case 'NO_PROVIDER':
      return 'no operator is selling it yet';
    case 'PROVIDER_NOT_VERIFIED':
      return 'its operator is not currently approved';
    case 'STARTS_IN_THE_PAST':
      return 'it has already started';
    default:
      return reason.toLowerCase().replaceAll('_', ' ');
  }
}
