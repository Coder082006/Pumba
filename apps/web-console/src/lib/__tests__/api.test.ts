import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiFetch } from '../api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiFetch', () => {
  it('unwraps the SRS §9.2 success envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: async () => ({ data: { status: 'ok' }, meta: { request_id: 'r1' } }),
      })) as unknown as typeof fetch,
    );

    await expect(apiFetch('/health')).resolves.toEqual({ status: 'ok' });
  });

  it('raises with the stable error code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 409,
        headers: { get: () => null },
        json: async () => ({
          error: {
            code: 'QUOTE_EXPIRED',
            message: 'Quote has expired.',
            details: [],
            request_id: 'r2',
            retryable: false,
          },
        }),
      })) as unknown as typeof fetch,
    );

    await expect(apiFetch('/trips/1/confirm')).rejects.toMatchObject({ code: 'QUOTE_EXPIRED' });
  });
});
