'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { use, useCallback, useEffect, useRef, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import { CardPayment } from '@/components/payment/card-payment';
import { ApiRequestError } from '@/lib/api';
import { confirmBasket } from '@/lib/booking';
import { createIntent, failureMessage, type Payment } from '@/lib/payments';
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
 * **Three calls now, in the order §9.4.5 to §9.4.7 gives them.** Reserving
 * creates the bookings; the intent that follows it asks the gateway for a
 * client secret; the card is then typed into Stripe's own field and never into
 * this page (PM1, SAQ A).
 *
 * **The tourist is told what each step did.** Reserving holds seats and
 * charges nothing, and the page says so; paying charges the trip's own total,
 * and the button says that figure. A screen that blurred the two would leave a
 * tourist unsure whether their card had been taken.
 *
 * Every refusal is shown as what it means rather than as a code: an expired
 * offer invites a new price, an operator that cannot sell right now names the
 * item, a declined card names the bank's reason (§21.8).
 */
export default function CheckoutPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [trip, setTrip] = useState<Trip | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [details, setDetails] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [payment, setPayment] = useState<Payment | null>(null);
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
      // §9.4.7 immediately: the seats are held for the payment window, and a
      // tourist who has to press a second button to start paying is a tourist
      // watching that window run down.
      setPayment(await createIntent(id, 'CARD', attempt.current));
    } catch (error) {
      setProblem(explain(error));
      setDetails(detailLines(error));
    } finally {
      setBusy(false);
    }
  }, [id, quote]);

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
          {payment ? (
            <PaymentStep payment={payment} tripId={id} onPaid={() => router.push(`/trips/${id}/confirmation`)} />
          ) : (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => void reserve()}
                className="w-full rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-colors duration-fast ease-out hover:bg-primary/90 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {busy ? 'Reserving…' : 'Reserve and pay'}
              </button>
              <p className="text-xs text-muted-foreground">
                Reserving holds your places and charges nothing. You pay on the next step, and each
                booking keeps the cancellation policy it is sold under today.
              </p>
            </>
          )}
        </section>
      ) : null}
    </div>
  );
}

/**
 * The paying half of the page — §24.22.
 *
 * Split out rather than inlined because it has its own failure to report: a
 * card that is declined is not a checkout that failed, it is a payment to try
 * again, and the reserved bookings are still there either way.
 */
function PaymentStep({
  payment,
  tripId,
  onPaid,
}: {
  payment: Payment;
  tripId: string;
  onPaid: () => void;
}) {
  const secret = payment.action?.payload?.client_secret;

  if (payment.status === 'FAILED') {
    return (
      <div className="space-y-3">
        <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
          {failureMessage(payment.failure_code)}
        </p>
        <Link
          href={`/trips/${tripId}/checkout`}
          className="inline-block text-sm font-medium text-primary hover:underline"
        >
          Try again
        </Link>
      </div>
    );
  }

  if (!secret) {
    // An intent with no action is one the gateway is still thinking about, or
    // one already captured. Either way the answer arrives by webhook, and the
    // confirmation page is where it shows up.
    return (
      <div className="space-y-3 text-sm">
        <p>Your payment is being processed.</p>
        <Link
          href={`/trips/${tripId}/confirmation`}
          className="inline-block font-medium text-primary hover:underline"
        >
          See your bookings
        </Link>
      </div>
    );
  }

  return <CardPayment payment={payment} clientSecret={secret} onPaid={onPaid} />;
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
