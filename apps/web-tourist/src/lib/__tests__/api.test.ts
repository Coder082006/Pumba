import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError, apiFetch } from '../api';

function mockResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k: string) => headers[k] ?? null },
    json: async () => body,
  } as unknown as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiFetch', () => {
  it('unwraps the SRS §9.2 success envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        mockResponse(200, { data: { status: 'ok' }, meta: { request_id: 'r1' } }),
      ),
    );

    // Callers get the resource, never `response.data.data`.
    await expect(apiFetch('/health')).resolves.toEqual({ status: 'ok' });
  });

  it('raises with the stable error code rather than the message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        mockResponse(409, {
          error: {
            code: 'INVENTORY_UNAVAILABLE',
            message: 'This departure is no longer available.',
            details: [{ field: 'items[1].departure_id', issue: 'SOLD_OUT' }],
            request_id: 'r2',
            retryable: false,
          },
        }),
      ),
    );

    // SRS §32.5 gives each booking-path conflict distinct treatment, so the
    // client must switch on `code`, not on wording that may be localised.
    await expect(apiFetch('/trips/1/quote')).rejects.toMatchObject({
      code: 'INVENTORY_UNAVAILABLE',
      status: 409,
      retryable: false,
      details: [{ field: 'items[1].departure_id', issue: 'SOLD_OUT' }],
    });
  });

  it('survives an unreadable error body without masking the failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 502,
        headers: { get: () => 'req-9' },
        json: async () => {
          throw new SyntaxError('not json');
        },
      })) as unknown as typeof fetch,
    );

    const error = await apiFetch('/health').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).retryable).toBe(true);
  });

  it('sends the Idempotency-Key header when one is supplied', async () => {
    // Typed as fetch so the recorded call tuple carries (input, init) rather
    // than the empty tuple an untyped vi.fn() infers.
    const fetchMock = vi.fn<typeof fetch>(async () =>
      mockResponse(201, { data: {}, meta: {} }),
    );
    vi.stubGlobal('fetch', fetchMock);

    // SRS §9.1 requires it on every POST that creates a booking, payment or
    // assignment.
    await apiFetch('/payments/intents', {
      method: 'POST',
      body: { trip_id: 'x' },
      idempotencyKey: 'key-1',
    });

    const init = fetchMock.mock.calls[0]?.[1];
    expect((init?.headers as Record<string, string>)['Idempotency-Key']).toBe('key-1');
  });
});
