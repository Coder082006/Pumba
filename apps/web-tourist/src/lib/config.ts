import 'server-only';

import { apiFetch } from '@/lib/api';

/**
 * `GET /config` — SRS §23.13, §24.1, §35.
 *
 * One module for the route, rather than one per consumer. The payload is a
 * closed allow-list on the server (`apps/common/public_config.py`), and two
 * modules each declaring their own view of it would drift from that list in
 * different directions — which is exactly the failure the API-side
 * `test_the_documented_response_matches_what_is_served` exists to catch, so
 * reintroducing it here would be perverse.
 *
 * ---
 *
 * Where the base map comes from — ADR 0016, Appendix D9, SRS §24.1.
 *
 * Read from `GET /config`, which SRS §23.13 and §24.1 already specify as the
 * route delivering client configuration. The `map.tile_url` and
 * `map.tile_attribution` `system_setting` rows are therefore the single source
 * of truth, which is what makes changing tile provider an administrator action
 * rather than a redeployment of this application — the promise ADR 0016 makes
 * and that an environment variable here would have quietly broken.
 *
 * Fetched on the server, never in the browser: a commercial provider
 * authenticates with a key in the tile URL, and §30.9 forbids a secret
 * reaching source control or an image. A key in a client bundle is published
 * to every visitor.
 *
 * **There is deliberately no hardcoded fallback.** An unreachable `/config`
 * must not silently serve OpenStreetMap tiles, because OSM's usage policy does
 * not permit commercial production traffic — a fallback would turn an outage
 * into a licence breach that nobody notices. §24.1 names the correct
 * resilience mechanism instead: *"falling through to cached config"*. The
 * revalidating cache below is that mechanism, so a transient failure serves
 * the last known-good values and a sustained one is a visible error rather
 * than a quiet substitution.
 */
export interface MapConfig {
  tileUrl: string;
  attribution: string;
}

/** BR-101's bound on a stay anchor — §24.11. */
export interface StayLimits {
  maxNights: number;
}

interface ConfigResponse {
  min_supported_version: string;
  enabled_currencies: string[];
  map_tile_url: string;
  map_tile_attribution: string;
  stay_max_nights: number;
  features: Record<string, boolean>;
}

/**
 * Five minutes. Long enough that a page render almost never blocks on this,
 * short enough that an administrator changing provider sees it take effect
 * without anyone being paged.
 */
const REVALIDATE_SECONDS = 300;

function platformConfig(): Promise<ConfigResponse> {
  return apiFetch<ConfigResponse>('/config', {
    // `next` is Next.js's extension to RequestInit and is not in the DOM lib's
    // type, so it is attached here rather than widening `RequestOptions` for
    // one caller.
    next: { revalidate: REVALIDATE_SECONDS },
  } as Parameters<typeof apiFetch>[1]);
}

export async function mapConfig(): Promise<MapConfig> {
  const config = await platformConfig();

  return {
    tileUrl: config.map_tile_url,
    // Paired with the URL by the API, not reassembled here: attribution is a
    // licence term, and a client that could render one without the other
    // would be the bug ADR 0016 pairs them to prevent.
    attribution: config.map_tile_attribution,
  };
}

/**
 * BR-101's bound, from the server rather than from a literal here.
 *
 * NFR-M07 forbids a business constant in code, and 30 is one — it is the
 * Appendix B row `stay.max_nights`, and an administrator raising it must not
 * need a front-end release for the form to agree with the API. The same
 * number reaches `catalogue.domain.pricing.stay_nights` on the server, so the
 * form and the eventual 422 are bounded by one value and cannot disagree.
 *
 * Deliberately no fallback, for the same reason `mapConfig` has none: a
 * default here would be the hardcoded constant wearing a disguise, and it
 * would be *silently* wrong rather than visibly absent on the day somebody
 * changed the row.
 */
export async function stayLimits(): Promise<StayLimits> {
  const config = await platformConfig();
  return { maxNights: config.stay_max_nights };
}
