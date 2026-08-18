/**
 * Typed API client.
 *
 * Thin fetch wrapper over the generated contract rather than a heavyweight
 * generated client: the surface is small, and this keeps the SRS §9.2
 * envelope handling in one readable place.
 *
 * Two rules the rest of the app depends on:
 *   - Every response is unwrapped from its envelope, so callers see the
 *     resource and never `response.data.data`.
 *   - Every failure raises `ApiRequestError` carrying the stable `code` from
 *     SRS §32.3, because the booking-path conflicts each need distinct
 *     treatment (SRS §32.5) and switching on a message would be fragile.
 */

import type { ApiEnvelope, ApiError } from '@pumba/contracts';

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1';

export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Array<{ field: string; issue: string }>;
  readonly retryable: boolean;
  readonly requestId: string | null;

  constructor(status: number, body: ApiError['error']) {
    super(body.message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = body.code;
    this.details = body.details ?? [];
    this.retryable = body.retryable ?? false;
    this.requestId = body.request_id ?? null;
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  /** Required on POSTs that create a booking, payment or assignment (SRS §9.1). */
  idempotencyKey?: string;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, idempotencyKey, headers, ...rest } = options;

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: {
      'Content-Type': 'application/json',
      ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {}),
      ...headers,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

  const payload: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const error = (payload as ApiError | null)?.error;
    throw new ApiRequestError(
      response.status,
      error ?? {
        code: 'UNKNOWN',
        message: 'The server returned an unreadable error.',
        details: [],
        request_id: response.headers.get('X-Request-Id'),
        retryable: response.status >= 500,
      },
    );
  }

  return (payload as ApiEnvelope<T>).data;
}
