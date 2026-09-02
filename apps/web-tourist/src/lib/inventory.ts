/**
 * Departures and capacity, from the browser — SRS §9.3.2, §16.2, §17.1.
 *
 * **Every figure here is indicative, and the payload says so.** §17.1 I3:
 * *search may read cached or stale capacity; committing a booking may not.* The
 * server labels each row `INDICATIVE`, and the only place an authoritative
 * number exists is inside `POST /trips/{id}/quote`, under a row lock. Nothing
 * in this module may be used to promise anybody a seat — what it is for is
 * letting a tourist choose a date that is likely to work, and telling them why
 * one will not.
 *
 * The read is **public**, like the rest of the catalogue: a tourist compares
 * dates before signing in, and §24.10 is an indexable page.
 */

import { apiFetch } from '@/lib/api';

import type { components } from '@pumba/contracts';

export type Departure = components['schemas']['Departure'];

/** Why a departure cannot take a given party — the server's own vocabulary. */
export type UnbookableReason = NonNullable<Departure['unbookable']>;

export interface DepartureQuery {
  /** `YYYY-MM-DD`, inclusive. Defaults to today at the destination. */
  from?: string;
  /** `YYYY-MM-DD`, inclusive. Defaults to thirty days after `from`. */
  to?: string;
  /**
   * Party size. Supplying it turns the list into advice: each row comes back
   * saying whether *this* party may take it, which is what lets a calendar
   * grey out a date rather than letting a tourist discover the refusal at the
   * end of a booking flow.
   */
  pax?: number | undefined;
}

/**
 * `GET /activities/{reference}/departures`.
 *
 * `reference` is a slug or a UUID — the same two forms the activity page
 * itself is addressed by, because §24.8 serves pages from slugs and §7.2 makes
 * the UUID the identifier the API exchanges.
 */
export function listDepartures(
  reference: string,
  query: DepartureQuery = {},
): Promise<Departure[]> {
  const search = new URLSearchParams();
  if (query.from) search.set('from', query.from);
  if (query.to) search.set('to', query.to);
  if (query.pax !== undefined) search.set('pax', String(query.pax));
  const suffix = search.size > 0 ? `?${search}` : '';
  return apiFetch<Departure[]>(
    `/activities/${encodeURIComponent(reference)}/departures${suffix}`,
  );
}

/**
 * What to tell a tourist about a departure they cannot take.
 *
 * The server's reasons are stable identifiers rather than sentences (§9.2), so
 * the words live here — and they are deliberately different sentences leading
 * to different actions. "Sold out" says try another date; "too late to book"
 * says any date but this one; "too many people" says no date will work, which
 * is the one a calendar must not let somebody discover four dates later.
 */
export const UNBOOKABLE_COPY: Record<string, string> = {
  SOLD_OUT: 'Sold out',
  CANCELLED: 'Cancelled',
  CLOSED: 'Not selling',
  PAST_CUTOFF: 'Too late to book',
  PARTY_TOO_SMALL: 'Below the minimum party',
  PARTY_TOO_LARGE: 'Too many people',
};

export function unbookableLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return UNBOOKABLE_COPY[reason] ?? 'Unavailable';
}

/**
 * Departures grouped by their local date at the destination.
 *
 * Grouped in the destination's zone rather than the browser's: a tourist
 * planning from London must see the 08:30 Zanzibar departure under the day it
 * leaves, and `toISOString().slice(0, 10)` would file an early-morning
 * departure under the previous date for anybody west of it.
 */
export function byLocalDate(
  departures: readonly Departure[],
  timeZone: string,
): Map<string, Departure[]> {
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const days = new Map<string, Departure[]>();
  for (const departure of departures) {
    const key = formatter.format(new Date(departure.departs_at));
    days.set(key, [...(days.get(key) ?? []), departure]);
  }
  return days;
}

/**
 * How urgent the remaining capacity is, for a client that wants to say so.
 *
 * Deliberately coarse. An exact "3 left" on every row invites a tourist to
 * treat an indicative number as a promise, and §17.1 I3 is explicit that it is
 * not one — but "only a few left" on a departure that genuinely has three
 * seats is true for as long as it takes to read.
 */
export function scarcity(remaining: number): 'gone' | 'few' | 'fine' {
  if (remaining <= 0) return 'gone';
  if (remaining <= 3) return 'few';
  return 'fine';
}
