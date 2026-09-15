/**
 * The booking API, from the browser — SRS §9.3.5, §9.4.6, §24.20 to §24.24.
 *
 * Client-side for the reason `lib/trips` gives: the access token lives in
 * memory (ADR 0008), so nothing here can run in a Server Component.
 *
 * **Nothing here takes money.** Payment is Phase 8. `confirmBasket` creates the
 * bookings and holds the seats for the payment window; the screens that call it
 * say so rather than implying the tourist has paid.
 */

import { API_BASE_URL, ApiRequestError, apiFetch, type RequestOptions } from '@/lib/api';
import { currencyHeaders } from '@/lib/currency';
import { authHeaders } from '@/lib/session';

import type { components } from '@pumba/contracts';

export type Basket = components['schemas']['Basket'];
export type Booking = components['schemas']['Booking'];
export type BookingDetail = components['schemas']['BookingDetail'];
export type Cancellation = components['schemas']['Cancellation'];
export type TripCancellation = components['schemas']['TripCancellation'];

function authed<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return apiFetch<T>(path, { ...options, headers: { ...authHeaders(), ...options.headers } });
}

/**
 * §9.4.6: turn an accepted quote into a basket.
 *
 * The `Idempotency-Key` is generated here, as `quoteTrip` does, and exposed so
 * a retry of the *same* attempt after a timeout gets the first basket back
 * rather than a `409 TRIP_NOT_PAYABLE` for a basket it already created.
 */
export function confirmBasket(
  tripId: string,
  quoteToken: string,
  key: string = crypto.randomUUID(),
): Promise<Basket> {
  return authed<Basket>(`/trips/${tripId}/confirm`, {
    method: 'POST',
    idempotencyKey: key,
    body: { quote_token: quoteToken },
  });
}

export function listBookings(filter: { trip?: string; status?: string } = {}): Promise<Booking[]> {
  const query = new URLSearchParams();
  if (filter.trip) query.set('trip', filter.trip);
  if (filter.status) query.set('status', filter.status);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  return authed<Booking[]>(`/bookings${suffix}`);
}

export function getBooking(bookingId: string): Promise<BookingDetail> {
  return authed<BookingDetail>(`/bookings/${bookingId}`);
}

export function previewCancellation(bookingId: string): Promise<Cancellation> {
  return authed<Cancellation>(`/bookings/${bookingId}/cancellation-preview`);
}

export function cancelBooking(bookingId: string, reason = ''): Promise<Cancellation> {
  return authed<Cancellation>(`/bookings/${bookingId}/cancel`, {
    method: 'POST',
    body: { reason },
  });
}

export function previewTripCancellation(tripId: string): Promise<TripCancellation> {
  return authed<TripCancellation>(`/trips/${tripId}/cancellation-preview`);
}

export function cancelTripBookings(tripId: string): Promise<TripCancellation> {
  return authed<TripCancellation>(`/trips/${tripId}/cancel`, { method: 'POST' });
}

/**
 * Download a booking's voucher — ADR 0026.
 *
 * Not `apiFetch`: the answer is a PDF, not a JSON envelope. An error still is
 * one, so a failure is read as JSON and raised as the same `ApiRequestError`
 * every other call raises, and a screen can treat it identically.
 */
export async function downloadVoucher(bookingId: string): Promise<{ filename: string; blob: Blob }> {
  const response = await fetch(`${API_BASE_URL}/bookings/${bookingId}/voucher`, {
    method: 'POST',
    headers: { ...currencyHeaders(), ...authHeaders() },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      error?: ConstructorParameters<typeof ApiRequestError>[1];
    } | null;
    throw new ApiRequestError(
      response.status,
      payload?.error ?? {
        code: 'UNKNOWN',
        message: 'The voucher could not be downloaded.',
        details: [],
        request_id: response.headers.get('X-Request-Id'),
        retryable: response.status >= 500,
      },
    );
  }
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `voucher-${bookingId}.pdf`;
  return { filename, blob: await response.blob() };
}

/** Statuses a tourist reads as "this part of my trip is going ahead". */
export const GOING_AHEAD = new Set(['CONFIRMED', 'AWAITING_PROVIDER', 'IN_PROGRESS', 'COMPLETED']);

/** Plain words for a booking status, for a chip rather than an enum. */
export function statusLabel(status: string): string {
  switch (status) {
    case 'PENDING':
      return 'Reserved, awaiting payment';
    case 'AWAITING_PROVIDER':
      return 'Waiting for the operator';
    case 'CONFIRMED':
      return 'Confirmed';
    case 'IN_PROGRESS':
      return 'Happening now';
    case 'COMPLETED':
      return 'Completed';
    case 'CANCELLED':
      return 'Cancelled';
    case 'REFUNDED':
      return 'Refunded';
    case 'NO_SHOW':
      return 'Missed';
    case 'FAILED':
      return 'Could not be booked';
    default:
      return status;
  }
}
