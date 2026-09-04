/**
 * `quoteTrip` sends an `Idempotency-Key` — SRS §9.4.5.
 *
 * The header is **required** by the server, so a client that omits it does not
 * degrade: it gets `422 IDEMPOTENCY_KEY_REQUIRED` and the planner's footer
 * reports that the trip could not be priced. That is exactly what happened when
 * the requirement landed on the API and this call site was not updated — 193
 * passing web tests and a green API suite, and "Get a price" broken in the
 * browser, because nothing tested the two halves against each other.
 *
 * So this file tests the request rather than the response. It is deliberately
 * about the header and nothing else, because the header is the part that no
 * other test on either side of the wire can see.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { quoteTrip } from '../trips';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function headersOf(call: number = 0): Record<string, string> {
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify({ data: {}, meta: {} }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('quoteTrip', () => {
  it('sends an Idempotency-Key', async () => {
    await quoteTrip('trip-1');
    expect(headersOf()['Idempotency-Key']).toBeDefined();
  });

  it('generates a well-formed key when the caller supplies none', async () => {
    /** The server caps the key at 64 characters; a UUID is 36. */
    await quoteTrip('trip-1');
    expect(headersOf()['Idempotency-Key']).toMatch(UUID);
  });

  it('uses a fresh key for each press of the button', async () => {
    /** Each press is a new offer the tourist asked for. Reusing one key would
     * hand them the first quote forever, including after they edited the trip. */
    await quoteTrip('trip-1');
    await quoteTrip('trip-1');
    expect(headersOf(0)['Idempotency-Key']).not.toBe(headersOf(1)['Idempotency-Key']);
  });

  it('honours a key the caller passes', async () => {
    /** The retry path: a request that timed out may well have succeeded
     * unseen, and repeating it with the original key returns that first answer
     * rather than holding a second set of seats. */
    await quoteTrip('trip-1', 'retry-of-the-same-attempt');
    expect(headersOf()['Idempotency-Key']).toBe('retry-of-the-same-attempt');
  });

  it('still POSTs to the trip it was given', async () => {
    /** The control: a test that only inspected headers would pass on a
     * function that had stopped calling the right endpoint. */
    const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>;
    await quoteTrip('trip-42');
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/trips/trip-42/quote');
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');
  });
});
