/**
 * Generated API contract.
 *
 * `schema.d.ts` is produced from `openapi/openapi.yaml`, which is itself
 * generated from the Django source by drf-spectacular. Neither file is
 * hand-edited: change the API in Python, regenerate, and commit the diff.
 * SRS §36.2 — "contracts/openapi/ is generated, committed and diffed in
 * review, which makes any breaking API change visible in the pull request."
 */

export type { components, operations, paths } from './schema';

import type { components } from './schema';

/** Success envelope of SRS §9.2. */
export interface ApiEnvelope<T> {
  data: T;
  meta: {
    request_id: string | null;
    next_cursor?: string | null;
  };
}

/** Error envelope of SRS §9.2. */
export interface ApiError {
  error: {
    /** Stable and machine-readable; the catalogue is SRS §32.3. */
    code: string;
    /** Safe to display to an end user, localised by Accept-Language. */
    message: string;
    details: Array<{ field: string; issue: string }>;
    request_id: string | null;
    retryable: boolean;
  };
}

/**
 * Money as it crosses the wire (SRS §9.1).
 *
 * `amount` is a decimal *string*, never a number: JSON numbers are IEEE 754
 * doubles and would silently corrupt a price. Parse with a decimal library,
 * never with `parseFloat`.
 */
export interface Money {
  amount: string;
  currency: string;
}

export type HealthResponse = components['schemas']['HealthResponse'];
