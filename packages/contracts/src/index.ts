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

/**
 * Catalogue read models — SRS §9.3.2.
 *
 * Aliases, not redefinitions. Each one points at the generated schema, so a
 * field that changes in Python changes here on the next `pnpm run openapi`
 * and every consumer fails to compile rather than reading `undefined` at
 * runtime. Adding a hand-written interface that "matches" one of these would
 * defeat the whole arrangement — §36.2 wants the breaking change visible in
 * the pull request.
 */
export type Country = components['schemas']['Country'];
export type Market = components['schemas']['Market'];
export type MarketRef = components['schemas']['MarketRef'];
export type Region = components['schemas']['Region'];
export type Destination = components['schemas']['Destination'];
export type Attraction = components['schemas']['Attraction'];
export type Activity = components['schemas']['Activity'];
export type Accommodation = components['schemas']['Accommodation'];
export type Tag = components['schemas']['Tag'];
export type CancellationPolicy = components['schemas']['CancellationPolicy'];
export type CancellationPolicyTier = components['schemas']['CancellationPolicyTier'];
export type Media = components['schemas']['Media'];
export type SearchHit = components['schemas']['SearchHit'];

/** §7.5.6's gateway discriminator, and §14's property and confirmation enums. */
export type GatewayType = components['schemas']['GatewayTypeEnum'];
export type PropertyType = components['schemas']['PropertyTypeEnum'];
export type ConfirmationMode = components['schemas']['ConfirmationModeEnum'];

/**
 * A keyset-paginated list response (SRS §9.1).
 *
 * `next_cursor` is opaque and must be passed back verbatim. It encodes the
 * ordering it was issued under, so a cursor from one `?sort=` replayed against
 * another is refused rather than silently returning a page with rows missing —
 * do not parse it, construct it, or carry it across a sort change.
 */
export type PagedResponse<T> = ApiEnvelope<T[]> & {
  meta: { next_cursor: string | null };
};

/**
 * BR-127. `rating_avg` is `null` whenever there are too few published reviews
 * to state a mean, and the server never sends the number in that case — so
 * rendering "New" is the only thing a client *can* do, rather than a rule it
 * has to remember. `rating_count` still arrives. See ADR 0015.
 */
export function hasDisplayableRating(
  activity: Pick<Activity, 'rating_avg'>,
): activity is Pick<Activity, 'rating_avg'> & { rating_avg: string } {
  return activity.rating_avg !== null && activity.rating_avg !== undefined;
}
