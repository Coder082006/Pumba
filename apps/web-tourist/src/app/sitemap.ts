import type { MetadataRoute } from 'next';

import { listActivities, listAttractions, listDestinations } from '@/lib/catalogue';
import { listAll } from '@/lib/paginate';

/**
 * `sitemap.xml` — SRS §24.8, §41.12.
 *
 * Built from the API at request time, not from a build-time snapshot. §4.1
 * requires a market to open without a deployment, and §41.12 makes that
 * concrete: an administrator publishes Arusha and it becomes browsable. A
 * sitemap baked at build time would leave the new destination invisible to
 * crawlers until the next release, which is the same failure as a hardcoded
 * catalogue wearing a different hat.
 *
 * **Every page of every list is followed.** `?limit` is bounded by
 * `page.max_size`, so a single call returns one page — and a sitemap listing
 * only the first page of each kind would look entirely normal while hiding
 * most of the catalogue. `listAll` throws rather than truncating, because a
 * partial sitemap is indistinguishable from a complete one.
 *
 * **The visibility filter is the API's, not this file's.** These endpoints
 * already exclude soft-deleted, inactive and unlaunched rows through
 * `domain.visibility`, and Pemba's absence from `/destinations` is asserted
 * there. Re-filtering here would be a second implementation of the rule that
 * could disagree with the first; consuming the same endpoints a tourist
 * consumes means the sitemap cannot advertise a page that 404s.
 *
 * **No `lastModified`.** The public serializers carry no `updated_at`, and the
 * available alternative — stamping `new Date()` on every entry — would tell a
 * crawler that the entire catalogue changed on every fetch. That is worse than
 * omitting the field: it is a signal search engines learn to distrust, and the
 * field is optional precisely so it can be left out when it is not known.
 */
/**
 * Rendered per request, **not** prerendered at build.
 *
 * `revalidate = 3600` was the obvious choice and is wrong here, which the
 * build output showed: Next prerenders the route at build time, when no API is
 * reachable, so the empty document that produced would be served — and cached
 * — for the first hour after every deployment. A crawler arriving in that
 * window sees a site with no pages, and nothing anywhere reports a problem.
 * That is §41.12's promise failing in the one way nobody would notice.
 *
 * Per-request rendering is not the expense it looks like. The underlying
 * fetches carry `next: { revalidate: 30 }` from `lib/catalogue`, so Next's
 * data cache absorbs the load and a burst of crawler requests costs one walk
 * of the catalogue every 30 seconds rather than one each.
 */
export const dynamic = 'force-dynamic';

/**
 * Absolute URLs, because a sitemap requires them.
 *
 * The localhost fallback is a development convenience and would be a real
 * defect in production — a sitemap of `http://localhost:3000/…` advertises
 * nothing. `NEXT_PUBLIC_SITE_URL` is deployment configuration, not a secret,
 * and `robots.ts` reads the same value so the two documents cannot disagree
 * about which host they describe.
 */
const BASE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000';

/** Bounded by `page.max_size` server-side; a larger ask is silently clamped. */
const PAGE_SIZE = 100;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // Each kind is fetched independently and a failure in one must not empty the
  // others: a sitemap missing its activities is a degraded sitemap, while one
  // that threw is no sitemap at all, and a 500 here removes every page from
  // the crawler's view rather than one section.
  //
  // Accommodation is deliberately not fetched. §7.7 of the plan lists it, but
  // ADR 0013 deferred the detail screen (§24.12, §24.13) to v2, so there is no
  // URL for a property to point at — and a sitemap entry for a page that does
  // not exist is worse than an omission: it teaches a crawler that this
  // document is unreliable. §24.11 reaches properties through `/stays`, which
  // is not indexed because it is a planning tool rather than an SEO surface.
  const [destinations, attractions, activities] = await Promise.all([
    collect(() => listAll((cursor) => listDestinations({ limit: PAGE_SIZE, cursor }))),
    collect(() => listAll((cursor) => listAttractions({ limit: PAGE_SIZE, cursor }))),
    collect(() => listAll((cursor) => listActivities({ limit: PAGE_SIZE, cursor }))),
  ]);

  if (![destinations, attractions, activities].some((section) => section.ok)) {
    // Every section failed, which is an outage rather than an empty
    // catalogue. Throwing means Next serves an error and retries on the next
    // request; returning the two static entries would publish a sitemap
    // asserting the site has no destinations, and a crawler that believes it
    // drops the pages it already knows.
    throw new Error('Every catalogue section failed; refusing to serve a sitemap that omits them.');
  }

  return [
    { url: `${BASE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${BASE_URL}/explore`, changeFrequency: 'daily', priority: 0.9 },
    ...entries('destinations', destinations.rows, 0.8),
    ...entries('attractions', attractions.rows, 0.7),
    ...entries('activities', activities.rows, 0.7),
  ];
}

function entries(
  segment: string,
  rows: { slug: string }[],
  priority: number,
): MetadataRoute.Sitemap {
  return rows.map((row) => ({
    url: `${BASE_URL}/${segment}/${row.slug}`,
    changeFrequency: 'weekly' as const,
    priority,
  }));
}

/**
 * A failed section yields nothing rather than failing the whole document —
 * a sitemap missing its activities is degraded, one that threw is absent.
 *
 * `ok` is what keeps that from hiding a total outage: three empty sections
 * because the catalogue is empty and three empty sections because the API is
 * down produce identical XML, and only one of them is correct to serve. The
 * caller distinguishes them.
 */
async function collect<T>(load: () => Promise<T[]>): Promise<Section<T>> {
  try {
    return { ok: true, rows: await load() };
  } catch {
    return { ok: false, rows: [] };
  }
}

interface Section<T> {
  ok: boolean;
  rows: T[];
}
