'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useState } from 'react';
import { LocalTime, Money } from '@pumba/ui';

import { ApiRequestError } from '@/lib/api';
import { downloadVoucher, listBookings, statusLabel, type Booking } from '@/lib/booking';
import { getTrip, type Trip } from '@/lib/trips';

/**
 * Booking Confirmation — SRS §24.23.
 *
 *   Purpose: close the loop with certainty. Components: success state, trip
 *   reference, per-component confirmation list with references,
 *   what-happens-next block, add-to-calendar, download-itinerary, share, Go to
 *   My Trips. States: partial success (one component AWAITING_PROVIDER) is
 *   shown honestly with the expected response deadline.
 *
 * **"Certainty" means saying exactly what is true.** In Phase 7 a tourist
 * arrives here after reserving, before paying, so the success state is
 * "reserved", not "booked", and the page names what happens next. Once a
 * booking is confirmed its voucher can be downloaded from here.
 *
 * Two §24.23 components are not built and are named rather than faked. The
 * downloadable itinerary is the emailed PDF §41.10 attaches to
 * TRIP_CONFIRMED, which needs payment (Phase 8). Share needs a public view of a
 * private trip, which nothing specifies. **No offline pack**: ADR 0002 amended
 * §41.10, and the web client "must not imply" an offline guarantee.
 */
export default function ConfirmationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [bookings, setBookings] = useState<Booking[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const [loaded, rows] = await Promise.all([getTrip(id), listBookings({ trip: id })]);
        setTrip(loaded);
        setBookings(rows);
      } catch (error) {
        setProblem(
          error instanceof ApiRequestError && error.status === 404
            ? 'That trip could not be found.'
            : 'This trip could not be loaded just now.',
        );
      }
    })();
  }, [id]);

  const voucher = useCallback(async (booking: Booking) => {
    setDownloading(booking.id);
    try {
      const { filename, blob } = await downloadVoucher(booking.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch {
      setProblem('That voucher could not be downloaded just now. Try again in a moment.');
    } finally {
      setDownloading(null);
    }
  }, []);

  if (problem && !trip) {
    return (
      <div className="rounded-lg border border-border p-8 text-center">
        <p className="text-muted-foreground">{problem}</p>
        <Link href="/trips" className="mt-4 inline-block text-sm font-medium text-primary">
          Go to your trips
        </Link>
      </div>
    );
  }
  if (!trip || !bookings) return <div aria-hidden className="h-64 rounded-lg bg-muted" />;

  const zone = trip.destination.timezone;
  const awaitingPayment = bookings.some((b) => b.status === 'PENDING');
  const awaitingOperator = bookings.filter((b) => b.status === 'AWAITING_PROVIDER');
  const failed = bookings.filter((b) => b.status === 'FAILED');

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-2 text-center">
        <p className="text-sm font-medium text-primary">
          {awaitingPayment ? 'Reserved' : failed.length === bookings.length ? 'Not booked' : 'Booked'}
        </p>
        <h1 className="font-display text-3xl font-bold tracking-tight">
          {trip.title ?? trip.destination.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          Trip reference <span className="font-mono text-foreground">{trip.reference}</span>
        </p>
      </header>

      {problem ? (
        <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
          {problem}
        </p>
      ) : null}

      <section aria-labelledby="components" className="rounded-lg border border-border">
        <h2 id="components" className="border-b border-border px-6 py-3 font-semibold">
          Your bookings
        </h2>
        <ul className="divide-y divide-border">
          {bookings.map((booking) => (
            <li key={booking.id} className="flex flex-wrap items-start justify-between gap-3 px-6 py-4">
              <div className="space-y-1">
                <p className="font-medium">{booking.title || booking.booking_type}</p>
                <p className="text-sm text-muted-foreground">
                  <LocalTime value={booking.starts_at} timeZone={zone} /> ·{' '}
                  <span className="font-mono">{booking.reference}</span>
                </p>
                <p className="text-sm">
                  <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium">
                    {statusLabel(booking.status)}
                  </span>
                  {booking.status === 'AWAITING_PROVIDER' && booking.response_due_at ? (
                    <span className="ml-2 text-xs text-muted-foreground">
                      The operator answers by <LocalTime value={booking.response_due_at} timeZone={zone} />
                    </span>
                  ) : null}
                </p>
              </div>
              <div className="flex flex-col items-end gap-2 text-sm">
                <Money
                  value={{ amount: booking.gross_amount, currency: booking.currency }}
                  display={booking.gross_amount_display}
                  compact
                />
                {booking.status === 'CONFIRMED' ? (
                  <button
                    type="button"
                    disabled={downloading === booking.id}
                    onClick={() => void voucher(booking)}
                    className="font-medium text-primary hover:underline disabled:opacity-60"
                  >
                    {downloading === booking.id ? 'Preparing…' : 'Download voucher'}
                  </button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="next" className="space-y-2 text-sm">
        <h2 id="next" className="font-semibold">
          What happens next
        </h2>
        {awaitingPayment ? (
          <p className="text-muted-foreground">
            Your places are held while you pay. Payment arrives in the next release, so nothing has
            been charged — if the hold lapses, get a new price from your trip and reserve again.
          </p>
        ) : null}
        {awaitingOperator.length > 0 ? (
          <p className="text-muted-foreground">
            {awaitingOperator.length === 1 ? 'One booking waits' : `${awaitingOperator.length} bookings wait`}{' '}
            for the operator to accept. If they decline or do not answer in time, it is cancelled
            and refunded in full.
          </p>
        ) : null}
        {failed.length > 0 ? (
          <p className="text-muted-foreground">
            {failed.length === 1 ? 'One part' : `${failed.length} parts`} of your trip could not be
            secured and will be refunded in full. The rest goes ahead.
          </p>
        ) : null}
        <p className="text-muted-foreground">
          Every booking keeps the cancellation policy it was sold under. You can see what a
          cancellation would refund before you cancel.
        </p>
      </section>

      <p className="text-center">
        <Link href="/trips" className="text-sm font-medium text-primary hover:underline">
          Go to My Trips
        </Link>
      </p>
    </div>
  );
}
