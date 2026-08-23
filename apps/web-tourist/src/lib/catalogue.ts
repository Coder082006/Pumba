import 'server-only';

import type {
  Accommodation,
  Activity,
  Attraction,
  Destination,
  SearchHit,
  Tag,
} from '@pumba/contracts';

import { apiFetch, apiFetchPage, type Page } from '@/lib/api';

/**
 * Reads of the §9.3.2 public catalogue.
 *
 * Server-side only. These are the pages §29's NFR-P01 gate measures and §24.8
 * calls the SEO surface, so the markup a crawler receives has to contain the
 * content — fetching this in the browser would serve an empty shell to
 * Googlebot and to anyone on a slow connection.
 *
 * Every list is keyset-paginated (§9.1) and the cursor is opaque; see
 * `apiFetchPage`.
 */

/**
 * Thirty seconds.
 *
 * §4.1 requires a market to open without a deployment, so nothing here may be
 * baked in at build time. Short enough that activating Arusha shows up while
 * somebody is still watching the console; long enough that a crawl of two
 * hundred pages does not become two hundred round trips per section.
 */
const REVALIDATE_SECONDS = 30;

const cached = { next: { revalidate: REVALIDATE_SECONDS } } as Parameters<typeof apiFetch>[1];

export function listDestinations(params: { limit?: number; cursor?: string } = {}) {
  return apiFetchPage<Destination>(`/destinations${query(params)}`, cached);
}

export function getDestination(reference: string) {
  return apiFetch<Destination>(`/destinations/${encodeURIComponent(reference)}`, cached);
}

export function listAttractions(params: { destination?: string; limit?: number } = {}) {
  return apiFetchPage<Attraction>(`/attractions${query(params)}`, cached);
}

export function listActivities(
  params: { destination?: string; tags?: string[]; sort?: string; limit?: number } = {},
) {
  return apiFetchPage<Activity>(`/activities${query(params)}`, cached);
}

export function listAccommodation(params: { destination?: string; limit?: number } = {}) {
  return apiFetchPage<Accommodation>(`/accommodation${query(params)}`, cached);
}

export function listTags() {
  return apiFetch<Tag[]>('/tags', cached);
}

export function search(q: string, kind?: string) {
  return apiFetch<SearchHit[]>(`/search${query({ q, kind })}`, cached);
}

/** `?a=1&b=2`, or `''`. Skips absent values so no empty parameter is sent. */
function query(params: Record<string, string | number | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      search.set(key, value.join(','));
    } else {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : '';
}

export type { Page };
