/**
 * The payment API, from the browser — SRS §9.4.7, §9.3.7, §24.22.
 *
 * **No card detail ever passes through here.** PM1 puts the platform in PCI
 * SAQ A scope: the server creates an intent, the gateway returns a client
 * secret, and the card is typed into Stripe's own iframe. This module moves
 * references and statuses, never a number a fraudster would want.
 *
 * **And no amount.** BR-060 computes the charge server-side from the trip;
 * `createIntent` has nowhere to put one, which is the point — a field a client
 * could fill is a field somebody eventually fills with 8.34.
 *
 * Types come from `@pumba/contracts`, generated from the committed OpenAPI
 * document, so a field that changes shape server-side breaks the build here
 * rather than at runtime in front of a tourist.
 */

import { apiFetch, type RequestOptions } from '@/lib/api';
import { authHeaders } from '@/lib/session';

import type { components } from '@pumba/contracts';

export type Payment = components['schemas']['Payment'];
export type PaymentAction = components['schemas']['PaymentAction'];
export type PaymentMethodOption = components['schemas']['PaymentMethod'];

function authed<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return apiFetch<T>(path, { ...options, headers: { ...authHeaders(), ...options.headers } });
}

/**
 * `POST /payments/intents` — §9.4.7.
 *
 * The `Idempotency-Key` is generated here for the reason `quoteTrip`'s is:
 * §9.1 requires one on every POST that creates a payment, and a caller who has
 * to remember it is a caller who eventually does not. `key` is exposed so a
 * retry of *the same* attempt — after a timeout, where the first request may
 * well have succeeded unseen — is answered with the first payment rather than
 * taking a second one (TC-074).
 */
export function createIntent(
  tripId: string,
  method: 'CARD' | 'MOBILE_MONEY' = 'CARD',
  key: string = crypto.randomUUID(),
): Promise<Payment> {
  return authed<Payment>('/payments/intents', {
    method: 'POST',
    body: { trip_id: tripId, method },
    idempotencyKey: key,
  });
}

export function getPayment(paymentId: string): Promise<Payment> {
  return authed<Payment>(`/payments/${paymentId}`);
}

/**
 * `POST /payments/{id}/verify` — the waiting screen's own question.
 *
 * A tourist who has just finished a 3-D Secure challenge is looking at a page
 * that has no way of knowing whether the webhook arrived. This asks the server
 * to ask the gateway, so the answer comes from the PSP (PM4) rather than from
 * an optimistic guess in the browser.
 */
export function verifyPayment(paymentId: string): Promise<Payment> {
  return authed<Payment>(`/payments/${paymentId}/verify`, { method: 'POST' });
}

export function paymentMethods(): Promise<PaymentMethodOption[]> {
  return authed<PaymentMethodOption[]>('/payments/methods');
}

/** Statuses that mean the tourist's part is over, one way or the other. */
const SETTLED = new Set(['CAPTURED', 'FAILED', 'EXPIRED']);

export function isSettled(payment: Payment): boolean {
  return SETTLED.has(payment.status);
}

/**
 * What to say about a payment that failed — §21.8's taxonomy, in words.
 *
 * The codes are normalised server-side precisely so this mapping is short and
 * the same whichever gateway is behind it. Anything unlisted gets the honest
 * general sentence rather than a code the tourist cannot act on.
 */
export function failureMessage(code: string | null | undefined): string {
  switch (code) {
    case 'INSUFFICIENT_FUNDS':
      return 'Your bank declined the payment for insufficient funds. Try another card.';
    case 'CARD_DECLINED':
      return 'Your bank declined the card. Try another one, or contact your bank.';
    case 'AUTHENTICATION_FAILED':
      return 'The security check with your bank was not completed. You can try again.';
    case 'EXPIRED_INSTRUMENT':
      return 'That card has expired. Try another one.';
    case 'MOBILE_MONEY_TIMEOUT':
      return 'The prompt on your phone was not approved in time. You can try again.';
    case 'GATEWAY_UNAVAILABLE':
    case 'PSP_UNAVAILABLE':
      return 'Our payment provider is not responding. Your places are still held — try again in a moment.';
    default:
      return 'The payment did not go through. Nothing has been charged.';
  }
}
